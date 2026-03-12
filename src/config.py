from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Portkey
    portkey_api_key: str = ""
    portkey_base_url: str = "https://portkey.syngenta.com/v1"
    model_name: str = "@bedrock-aifoundry-euc1-001/global.anthropic.claude-opus-4-6-v1"

    # GitHub
    github_token: str = ""

    # Datadog
    dd_api_key: str = ""
    dd_app_key: str = ""
    dd_site: str = "datadoghq.eu"
    dd_cluster: str = ""

    # Jira
    jira_url: str = ""
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""

    # Slack
    slack_token: str = ""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""

    # Pipeline
    max_review_iterations: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
