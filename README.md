Genie is a programmable framework for creating task-oriented conversational
agents that are designed to handle complex user interactions and knowledge
access. Unlike LLMs, Genie provides reliable grounded responses, with 
controllable agent policies through its expressive specification, Genie 
Worksheet. In contrast to dialog trees, it is resilient to diverse user queries,
helpful with knowledge sources, and offers ease of programming policies through
 its declarative paradigm.

## Fork Origin

This repository is forked from the Stanford OVAL GenieWorksheets project:
`https://github.com/stanford-oval/genie-worksheets`.

## Insurance Lead Agent

This repository is currently focused on one end-to-end worksheet-based agent:
`experiments/agents/insurance_lead_followup`.

### Purpose

The `insurance_lead_followup` agent runs an outbound follow-up flow for home
insurance leads. It is designed to:

- confirm the caller reached the correct contact,
- verify quote details already on file,
- collect underwriting qualification fields,
- offer and book a consultation with a licensed agent,
- finalize and persist the call outcome.

### Worksheet Flow

The implementation is organized around a top-level worksheet (`Main`) and four
sub-worksheets in sequence:

1. `LeadContact`
   - Confirms right person and identity.
   - Captures whether the person gives permission to continue.
   - Handles early exits (`wrong person`, `callback`, `do_not_call`).
2. `VerifyQuote`
   - Verifies canonical quote fields:
     - `property_address`
     - `property_type`
     - `coverage_start`
   - These are confirmation-required fields.
3. `UnderwritingIntake`
   - Captures:
     - `roof_age_years`
     - `claims_past_five_years`
     - conditional `claims_details`
   - Computes/records underwriting disposition metadata.
4. `ScheduleConsultation`
   - Captures consultation interest, time preference, selected slot, and booking confirmation.
5. `Main.final_confirm`
   - Asks for last edits, then finalizes the call record.

### Files to Know

- Worksheet definitions: `experiments/agents/insurance_lead_followup/worksheets.py`
- Runtime builder (worksheet runtime): `experiments/agents/insurance_lead_followup/worksheet_agent_builder.py`
- API + DB sync logic: `experiments/agents/insurance_lead_followup/api.py`
- Local setup guide: `experiments/agents/insurance_lead_followup/local_setup/README.md`
- Chainlit app: `experiments/agents/insurance_lead_followup/frontend/app_insurance.py`


## Installation

To install Genie, we recommend using uv ([UV installation guide](https://github.com/astral-sh/uv?tab=readme-ov-file#installation))


```bash
git clone https://github.com/KevinHan1209/genie-worksheets.git
cd genie-worksheets
uv venv
source venv/bin/activate
uv sync
```

## Creating Agents

Example agents are present in `experiments/agents/` directory. You can use them
as a reference to create your own agents.

### Load the model configuration

```python
from worksheets import Config
import os

# Define path to the prompts
current_dir = os.path.dirname(os.path.realpath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")

# Load config from YAML file
config = Config.load_from_yaml(os.path.join(current_dir, "config.yaml"))
```

You can also define the configuration programmatically:

```python
from worksheets import Config, OpenAIModelConfig
import os

config = Config(
    semantic_parser=OpenAIModelConfig(
        model_name="gpt-4o",
    ),
    response_generator=OpenAIModelConfig(
        model_name="gpt-4o",
    ),
    knowledge_parser=OpenAIModelConfig(
        model_name="gpt-4o",
    ),
    knowledge_base=OpenAIModelConfig(
        model_name="gpt-4o",
    ),
)
```

### Define your API functions

```python
from worksheets.agent.config import agent_api

@agent_api("check_availability", "Get consultation slots")
def check_availability(day_preference=None):
    # Implementation here
    return [
        "Thursday 2:00 PM with James Rivera",
        "Friday 10:00 AM with Monica Chen",
    ]

@agent_api("book_appointment", "Book a consultation slot")
def book_appointment(selected_slot, **kwargs):
    # Implementation here
    return {"success": True, "selected_slot": selected_slot}
```

### Define your starting prompt

You can load your starting prompt from a template file:

```python
from worksheets.agent.builder import TemplateLoader

starting_prompt = TemplateLoader.load(
    os.path.join(current_dir, "starting_prompt.md"), format="jinja2"
)
```

Or define it inline:

```python
starting_prompt = """Hello, this is an automated assistant calling on behalf of Prestige Home Insurance.
I can help verify quote details, collect underwriting information, and schedule a consultation with a licensed agent.

How can I help you today?"""
```

### Define the Agent

```python
from worksheets import AgentBuilder, SUQLKnowledgeBase, SUQLReActParser

agent = (
    AgentBuilder(
        name="InsuranceLeadBot",
        description="You are an outbound follow-up assistant for Prestige Home Insurance.",
        starting_prompt=starting_prompt.render() if hasattr(starting_prompt, 'render') else starting_prompt,
    )
    .with_knowledge_base(
        SUQLKnowledgeBase,
        tables_with_primary_keys={
            "insurance_leads": "lead_id",
            "insurance_quotes": "quote_id",
            "insurance_underwriting": "underwriting_id",
            "insurance_appointments": "appointment_id",
            "insurance_call_log": "call_id",
        },
        database_name="insurance",
        embedding_server_address="http://127.0.0.1:8509",
        source_file_mapping={
            "insurance_general_info.txt": os.path.join(
                current_dir, "insurance_general_info.txt"
            )
        },
        db_username="select_user",
        db_password="select_user",
        db_host="127.0.0.1",
        db_port="5432",
    )
    .with_parser(
        SUQLReActParser,
        example_path=os.path.join(current_dir, "examples.txt"),
        instruction_path=os.path.join(current_dir, "instructions.txt"),
        table_schema_path=os.path.join(current_dir, "table_schema.txt"),
    )
    .with_gsheet_specification("YOUR_SPREADSHEET_ID_HERE")
    .build(config)
)
```

### Load the agent from a CSV file
You can also load the agent from a CSV file. Instead of using the `with_gsheet_specification` method, you can use the `with_csv_specification` method.

```python
from worksheets import AgentBuilder, SUQLKnowledgeBase, SUQLReActParser
import os

agent = (
    AgentBuilder(
        name="InsuranceLeadBot",
        description="You are an outbound follow-up assistant for Prestige Home Insurance.",
        starting_prompt=starting_prompt.render() if hasattr(starting_prompt, 'render') else starting_prompt,
    )
    .with_csv_specification(os.path.join(current_dir, "insurance_lead_spec.csv"))
    .build(config)
)
```

### Load the agent from a JSON file
You can also load the agent from a JSON file. Instead of using the `with_gsheet_specification` method, you can use the `with_json_specification` method.

```python
from worksheets import AgentBuilder, SUQLKnowledgeBase, SUQLReActParser
import os

agent = (
    AgentBuilder(
        name="InsuranceLeadBot",
        description="You are an outbound follow-up assistant for Prestige Home Insurance.",
        starting_prompt=starting_prompt.render() if hasattr(starting_prompt, 'render') else starting_prompt,
    )
    .with_json_specification(os.path.join(current_dir, "your_agent_spec.json"))
    .build(config)
)
```

A sample JSON spec is present in `experiments/agents/insurance_lead_followup/insurance_lead_spec.json`.

### Run the conversation loop

```python
import asyncio
from worksheets import conversation_loop

if __name__ == "__main__":
    # Run the conversation loop in the terminal
    asyncio.run(conversation_loop(agent, "output_state_path.json", debug=True))
```


### Add prompts
For each agent you need to create prompts for:
- Semantic parsing: `semantic_parser_stateful.prompt`
- Response generation: `response_generator.prompt`

Place these prompts in the prompt directory that you specify while creating the
agent.

You can copy basic annotated prompts from `experiments/sample_prompts/` 
directory. Make changes where we have `TODO`. You need to provide a few
guidelines in the prompt that will help the LLM to perform better and some 
examples. Please see `experiments/agents/insurance_lead_followup/prompts/` for inspiration!


### Spreadsheet Specification

To create a new agent, you should have a Google Service Account and create a new spreadsheet. 
You can follow the instructions [here](https://cloud.google.com/iam/docs/service-account-overview) to create a Google Service Account.
Share the created spreadsheet with the service account email.

You should save the service account key as `service_account.json` in the repository root.

Here is a starter worksheet that you can use for your reference: [Starter Worksheet](https://docs.google.com/spreadsheets/d/1ST1ixBogjEEzEhMeb-kVyf-JxGRMjtlRR6z4G2sjyb4/edit?usp=sharing)

Here is a sample spreadsheet for an insurance lead follow-up agent: [Insurance Lead Follow-up Agent](https://docs.google.com/spreadsheets/d/1FXg5VFrdxQlUyld3QmKKL9BN1lLIhAtQTJjCHyNOU_Y/edit?usp=sharing)

Please note that we only use the specification defined in the first sheet of the spreadsheet.

## LLM Config
You should create a `.env` file similar to `.env.example` and fill in the values for the LLM API keys and endpoints.

### Running the Agent (Web Interface)

Create a folder `frontend/`  under `experiments/agents/<agent_name>` and create a `app_*` file.

You can run the agent in a web interface by running:

**NOTE:** You should run the agent in the `frontend` directory to preserve the frontend assets.

For insurance lead follow-up agent:
```bash
cd experiments/agents/insurance_lead_followup/frontend/
chainlit run app_insurance.py --port 8801
```
