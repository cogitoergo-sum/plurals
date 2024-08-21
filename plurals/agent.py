import warnings
from typing import Optional, Dict

import pandas as pd
from litellm import completion

from plurals.helpers import *

DEFAULTS = load_yaml("instructions.yaml")


def _load_global_anes_data():
    """
    Load global ANES data for the agent. As per codebook page 2 section 1, the cases that don't have weights are
    not meant for population inference.

    Here is the codebook url:
    https://electionstudies.org/wp-content/uploads/2024/03/anes_pilot_2024_userguidecodebook_20240319.pdf

    Loads:
    - PERSONA_MAPPING: Mapping for converting dataset rows to persona descriptions.
    - DATASET: ANES dataset for persona and ideological queries
    """
    global PERSONA_MAPPING, DATASET
    PERSONA_MAPPING = load_yaml("anes-mapping.yaml")
    DATASET = pd.read_csv(
        os.path.join(
            os.path.dirname(__file__),
            'data',
            'anes_pilot_2024_20240319.csv'),
        low_memory=False)
    DATASET.dropna(subset=['weight'], inplace=True)
    DATASET['age'] = 2024 - DATASET['birthyr']


_load_global_anes_data()


class Agent:
    """
    Agents are LLMs with customizable personas, who complete tasks with other Agents working together in Structures.
    Personas of Agents can be instantiated directly, null (i.e., default system prompt), or leverage external datasets
    like ANES for nationally-representative personas.

    The main attributes of the Agent class are:

    1. `system_instructions`: Set either directly or through various persona methods.

    2. `combination_instructions`: Dictates how Agents should combine previous responses with the current task.

    3. `task`: The task (i.e., user prompt) that Agents respond to.

    Agents can be used alone or in conjunction with Structures to create multi-agent simulations. When used with
    Structures, the Structure-level attribute values will override the Agent-level attribute values. Eg: If you set a task
    at an agent level and a Structure-level, the Structure-level task will be used.

    Args:
        task (Optional[str]): Description of the task for the agent to process. If the agent is part of a structure,
            and the task is provided to the structure, then the agent will inherit that task.
        combination_instructions (Optional[str]): Instructions for combining previous responses with the current
            task. If the agent is part of a structure and the combination_instructions are provided to the structure,
            then the agent will inherit those combination instructions. Must include a ${previous_responses}
            placeholder.
        ideology (Optional[str]): Ideological perspective to influence persona creation, supported values are
                                  ['liberal', 'conservative', 'moderate', 'very liberal', 'very conservative'].
        query_str (Optional[str]): Custom query string for filtering the ANES dataset according to specific criteria.
        model (str): The language model version to use for generating responses.
        system_instructions (Optional[str]): Overrides automated instructions with a custom set of directives for the
            model.
        persona_template (Optional[str]): Template string for constructing the persona. If passing your own, you must include a ${persona}
            placeholder. You can pass in the names of templates located in `instructions.yaml` [1]. If not supplied: When using an ANES-generated persona the `anes` template will be used.
            Otherwise, the `default` template will be used. Current templates: https://github.com/josh-ashkinaze/plurals/blob/main/plurals/instructions.yaml
        persona (Optional[str]): Direct specification of a persona description.
        kwargs (Optional[dict]): Additional keyword arguments for the model's completion function. These are provided by LiteLLM. Enter `help(litellm.completion)` for details. LiteLLM completion function: https://litellm.vercel.app/docs/completion/input#input-params-1

    Attributes:
        persona_mapping (Optional[Dict[str, Any]]): Dictionary to map dataset rows to persona descriptions.
        data (pd.DataFrame): Loaded dataset for persona and ideological queries.
        system_instructions (Optional[str]): Final system instructions for the agent to follow. The system
            instructions can be set directly or generated from a persona-based method.
        original_task_description (str): The original, unmodified task description.
        current_task_description (str): Dynamically updated task description that may include prior responses.
        history (list): Chronological record of prompts, responses, and models used during the agent's operation.
        info (dict): Comprehensive attributes of the agent's current configuration and state.
        responses (list): List of responses generated by the agent (the same information can be accessed by history)

    **Examples:**

        **Using Agent without a structure**.

        .. code-block:: python

            task = "Say hello"

            a = agent(task=task)
            ans = a.process()

            pirate_agent = Agent(system_instructions="You are a pirate.", model='gpt-4o', task=task)
            pirate_hello = pirate_agent.process()
            pirate_goodybe = pirate_agent.process(task="Now say a heartfelt goodybe.")

        **Manual system instructions**: This allows you to set system instructions to whatever you would like. Also note
        that you can pass in additional kwargs to the model.

        .. code-block:: python

            agent = Agent(system_instructions="You are a predictable independent", model='gpt-4o',
                          kwargs={'temperature': 0.1, 'max_tokens': 200})

        **No system instructions**: When no system instructions are inputted, agents will use default system instructions.

        .. code-block:: python

            agent = Agent(model='gpt-4o', kwargs={'temperature': 1, 'max_tokens': 500})

        **Persona Template**: When using a persona method, the full system instructions are a combination of the
        persona and the `persona_template`---where the latter is a template string that includes a ${persona}
        placeholder. This gives more instructions to the model of how to enact the persona.

        Here is an example of using the `empathetic` persona template with a persona of "a moderate voter from Michigan".
        You can see how the full system instructions are a combination of the two.

        .. code-block::

            empathetic_michigan = Agent(persona="a moderate voter from Michigan",
                persona_template="empathetic")
            print(empathetic_michigan.system_instructions)

        .. code-block:: text

            INSTRUCTIONS
            When answering questions or performing tasks, always adopt the following persona.

            PERSONA:
            a moderate voter from Michigan

            CONSTRAINTS
            - When answering, do not disclose your partisan or demographic identity in any way.
            - Think, talk, and write like your persona.
            - Use plain language.
            - Adopt the characteristics of your persona.
            - Be empathetic and compassionate.
            - Use narrative, rhetoric, testimony, storytelling and more emotional forms of communication instead of relying solely on facts. It is okay to deviate from relying solely on facts.

        Here is an example of using your own persona template with the appropriate ${persona} placeholder.

        .. code-block::

            agent = Agent(persona="a moderate voter from Michigan", persona_template="You are ${persona}.")
            print(agent.system_instructions)
            # You are a moderate voter from Michigan.

        We support several automatic ways of generating personas from American National Election Studies (ANES).

        **Ideology Matching (ANES Persona Method):** We support an ideology keyword that can be one of
        ['very liberal', 'liberal', 'moderate', 'conservative', 'very conservative'] where the 'veries' are a
        subset of the normals. The below example will pick a random row from ANES where
        the citizen identifies as `very conservative` and use that as the `persona`. We always use appropriate sampling
        weights, so personas will be nationally representative.

        .. code-block::

            agent = Agent(ideology="very conservative", model='gpt-4o', task=task)
            print(agent.persona)
            print(agent.system_instructions)

        .. code-block:: text

            Your age is 57. Your education is high school graduate. Your gender is man. Your race is hispanic. Politically, you identify as a(n) republican. Your ideology is very conservative. Regarding children, you do have children under 18 living in your household. Your employment status is full-time. Your geographic region is the northeast. You live in a suburban area. You live in the state of new york.

        .. code-block:: text

            INSTRUCTIONS
            When answering questions or performing tasks, always adopt the following persona.

            PERSONA:
            Your age is 57. Your education is high school graduate. Your gender is man. Your race is hispanic. Politically, you identify as a(n) republican. Your ideology is very conservative. Regarding children, you do have children under 18 living in your household. Your employment status is full-time. Your geographic region is the northeast. You live in a suburban area. You live in the state of new york.

            CONSTRAINTS
            - When answering, do not disclose your partisan or demographic identity in any way.
            - Think, talk, and write like your persona.
            - Use plain language.
            - Adopt the characteristics of your persona.
            - Do not be overly polite or politically correct.


        **Random Nationally Representative Personas (ANES Persona Method)**: If you make persona== 'random' then we will
        randomly sample a row
        from ANES and use that as the persona.

        .. code-block:: python

            agent = Agent(persona='random', model='gpt-4o', task=task)

        **Pandas Query String (ANES Persona Method):** If you want to get more specific, you can pass in a query string
        that will be used to filter the ANES dataset. Again, all ANES methods use sampling weights to ensure national representativeness.

        .. code-block:: python

            agent = Agent(query_str="inputstate=='West Virginia' & ideo5=='Very conservative'", model='gpt-4o', task=task)


        **Here is how to inspect exactly what is going on with Agents.** You can view an Agent's responses,
        the agent's `history` (prompts + responses), and `info`---which is a dictionary of the agent's attributes (such as system instructions).

        .. code-block:: python

            from plurals.agent import Agent
            a = Agent(ideology="very conservative", model='gpt-4o', task="A task here")
            a.process()

    """

    def __init__(self,
                 task: Optional[str] = None,
                 combination_instructions: Optional[str] = None,
                 ideology: Optional[str] = None,
                 query_str: Optional[str] = None,
                 model: str = "gpt-4o",
                 system_instructions: Optional[str] = None,
                 persona_template: Optional[str] = "default",
                 persona: Optional[str] = None,
                 kwargs: Optional[Dict[str, Any]] = None):
        self.model = model
        self.system_instructions = system_instructions
        self.combination_instructions = combination_instructions
        self._history = []
        self.persona_mapping = PERSONA_MAPPING
        self.task_description = task
        self.persona = persona
        self.ideology = ideology
        self.data = DATASET
        self.query_str = query_str
        self.original_task_description = task
        self.current_task_description = task
        self.defaults = DEFAULTS
        self.persona_template = persona_template
        self._validate_templates()
        self._validate_system_instructions()
        self._set_system_instructions()
        self.kwargs = kwargs if kwargs is not None else {}

    def _set_system_instructions(self):
        """
        Users can directly pass in system_instructions. Or, we can generate system instructions by combining a
        persona_template and a persona.

        In these two cases, persona_template does not do anything since system_instructions is set directly or is None.
        - If system_instructions is already provided, we don't need to do anything since system_instructions is
        already set.
        - If neither system_instructions, persona, ideology, nor query_str is provided (default) set
        system_instructions to None.

        Otherwise, we generate system instructions using a persona:
        - If persona is directly provided, use this.
        - If persona is "random" generate a random ANES persona
        - If persona is not already provided, generate it. This can be generated from a `query_str` or from `ideology`.
        """
        # If system_instructions is already provided, we don't need to do
        # anything
        if self.system_instructions is not None:
            return

        # If system_instructions, persona, ideology, nor query_str is provided,
        # set system_instructions to None
        if not self.system_instructions and not self.persona and not self.ideology and not self.query_str:
            self.system_instructions = None
            return

        # If persona is already provided, use it.
        if self.persona:
            self.persona = self.persona

        # If persona is "random" generate a random ANES persona
        if self.persona == "random":
            self.persona = self._generate_persona()

        # If persona is not already provided, generate it
        if not self.persona:
            self.persona = self._generate_persona()

        # Use the persona_template to create system_instructions
        self.persona_template = self.defaults['persona_template'].get(
            self.persona_template, self.persona_template).strip()
        self.system_instructions = SmartString(
            self.persona_template).format(
            persona=self.persona,
            task=self.task_description).strip()

    # noinspection PyTypeChecker
    def _generate_persona(self) -> str:
        """
        Generates a persona based on the provided data, ideology, or query string.

        Returns:
            str: Generated persona description.

        Sets:
            self.persona_template: Uses `anes` persona
        """
        self.persona_template = self.defaults['persona_template'].get("anes")
        if self.persona == "random":
            return self._get_random_persona(self.data)
        if self.ideology:
            filtered_data = self._filter_data_by_ideology(self.ideology)
            if filtered_data.empty:
                raise AssertionError("No data found satisfying conditions")
            selected_row = filtered_data.sample(
                n=1, weights=filtered_data['weight']).iloc[0]
            return self._row2persona(selected_row, self.persona_mapping)
        elif self.query_str:
            filtered_data = self.data.query(self.query_str)
            if filtered_data.empty:
                raise AssertionError("No data found satisfying conditions")
            selected_row = filtered_data.sample(
                n=1, weights=filtered_data['weight']).iloc[0]
            return self._row2persona(selected_row, self.persona_mapping)

    def process(
            self,
            task: Optional[str] = None,
            previous_responses: str = "",
    ) -> Optional[str]:
        """
        Process the task, optionally building upon a previous response. If you pass in a task, it will replace the
        Agent's initialized task description. If you pass in a previous responses, it will be incorporated into the task
        description if combination_instructions have not been set.

        Args:
            previous_responses (str): The previous responses to incorporate.
            task (Optional[str]): The task description to process. If not provided, the agent will use its current task.

        Returns:
            Optional[str]: The response from the LLM.
        """
        if task:
            self.set_task(task)

        if previous_responses:
            # Update the task description with the previous responses
            combined_responses = SmartString(
                self.combination_instructions).format(
                previous_responses=previous_responses)
            if self.current_task_description:
                self.current_task_description = SmartString(
                    f"{self.current_task_description}\n{combined_responses}")
            else:
                self.current_task_description = SmartString(
                    f"{self.original_task_description}\n{combined_responses}")
            self.current_task_description = self.current_task_description.strip()
        else:
            self.current_task_description = self.original_task_description
        return self._get_response(self.current_task_description)

    def _get_random_persona(self, data: pd.DataFrame) -> str:
        """
        Generates a random persona description based on the dataset.

        Args:
            data (pd.DataFrame): The dataset to use for generating persona descriptions.

        Returns:
            str: Generated persona description.
        """
        selected_row = data.sample(n=1, weights=data['weight']).iloc[0]
        return self._row2persona(selected_row, self.persona_mapping)

    def _get_response(self, task: str) -> Optional[str]:
        """
        Internal method to interact with the LLM API and get a response.

        Args:
            task (str): The task description to send to the LLM.

        Returns:
            Optional[str]: The response from the LLM.
        """
        if self.system_instructions:
            messages = [{"role": "system", "content": self.system_instructions}, {"role": "user", "content": task}]
        else:
            messages = [{"role": "user", "content": task}]
        try:
            response = completion(
                model=self.model,
                messages=messages,
                **self.kwargs)
            content = response.choices[0].message.content
            prompts = {
                'system': next((msg['content'] for msg in messages if msg['role'] == 'system'), None),
                'user': next((msg['content'] for msg in messages if msg['role'] == 'user'), None)}
            self._history.append(
                {'prompts': prompts, 'response': content, 'model': self.model})
            return content
        except Exception as e:
            print(f"Error fetching response from LLM: {e}")
            return None

    @staticmethod
    def _row2persona(row: pd.Series, persona_mapping: Dict[str, Any]) -> str:
        """
        Converts a dataset row into a persona description string.

        Args:
            row (pd.Series): The dataset row to convert.
            persona_mapping (Dict[str, Any]): Mapping to convert dataset rows into persona descriptions.

        Returns:
            str: Generated persona description.
        """
        persona = []
        for var, details in persona_mapping.items():
            value = row.get(var)

            if var == "age" and value is not None:
                value = int(value)

            if value is None or pd.isna(value) or (details.get('bad_vals') and str(value) in details['bad_vals']):
                continue

            if details.get('recode_vals') and str(value) in details['recode_vals']:
                value = details['recode_vals'][str(value)]

            clean_name = details['name']
            persona.append(f"{clean_name} {str(value).lower()}.")
        return " ".join(persona)

    def _filter_data_by_ideology(self, ideology: str) -> pd.DataFrame:
        """
        Filters the dataset based on the ideology.

        Args:
            ideology (str): The ideology to filter by.

        Returns:
            pd.DataFrame: The filtered dataset.
        """
        try:
            if ideology.lower() == 'liberal':
                return self.data[self.data['ideo5'].isin(
                    ['Liberal', 'Very liberal'])]
            elif ideology.lower() == 'conservative':
                return self.data[self.data['ideo5'].isin(
                    ['Conservative', 'Very conservative'])]
            elif ideology.lower() == 'moderate':
                return self.data[self.data['ideo5'] == 'Moderate']
            elif ideology.lower() == "very liberal":
                return self.data[self.data['ideo5'] == 'Very liberal']
            elif ideology.lower() == "very conservative":
                return self.data[self.data['ideo5'] == 'Very conservative']
            return pd.DataFrame()
        except Exception as e:
            raise AssertionError(f"Error filtering data by ideology: {e}")

    def _validate_system_instructions(self):
        """
        Validates the system instructions arguments.

        Errors raised if:
        - ideology or query_str is passed in without data and persona_mapping
        - system_instructions is passed in with persona or persona_template
        - ideology or query_str is passed in with persona
        - ideology is passed in and it's not in  ['liberal', 'conservative', 'moderate', 'very liberal',
        'very conservative']
        """
        if self.ideology or self.query_str:
            assert self.data is not None and self.persona_mapping is not None, ("If you use either `ideology` or "
                                                                                "`query_str` you need to provide both "
                                                                                "a dataframe and a persona mapping to "
                                                                                "process rows of the dataframe.")

        if (sum([bool(self.ideology), bool(self.query_str), bool(self.persona),
                 bool(self.system_instructions)]) > 1):
            raise AssertionError("You can only pass in one of ideology, query_str, system_instructions, or persona")

        if self.ideology:
            allowed_vals = [
                'liberal',
                'conservative',
                'moderate',
                'very liberal',
                'very conservative']
            assert self.ideology in allowed_vals, f"Ideology has to be one of: {str(allowed_vals)}"

    def _validate_templates(self):
        """
        Errors raised if:
        - a user passes in persona_template but it does not contain a persona placeholder (so there is no way to
        format it)
        """
        if self.persona_template:
            default_templates = list(self.defaults['persona_template'].keys())

            assert '${persona}' in self.persona_template or self.persona_template in default_templates, (
                    "If you pass in a persona_template, it must contain a ${persona} placeholder or be one of the default templates:" + str(
                default_templates))

    @property
    def history(self):
        if not self._history:
            warnings.warn("Be aware: No Agent history was found since tasks have not been processed yet.")
            return None
        else:
            return self._history

    @property
    def info(self):
        return {
            "original_task": self.original_task_description,
            "current_task_description": self.current_task_description,
            "system_instructions": self.system_instructions,
            "history": self.history,
            "persona": self.persona,
            "ideology": self.ideology,
            "query_str": self.query_str,
            "model": self.model,
            "persona_template": self.persona_template,
            "kwargs": self.kwargs}

    @property
    def responses(self):
        history = self.history
        if not history:
            warnings.warn("No history found. Please process a task first!")
            return None
        return [history[i]['response'] for i in range(len(history))]

    def __repr__(self):
        return str(self.info)

    def set_task(self, task: str):
        """
        Set the task description for the agent to process.

        Args:
            task (str): The new task description.
        """
        self.original_task_description = task
        self.current_task_description = task
