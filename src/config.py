from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Portkey
    portkey_api_key: str = ""
    portkey_base_url: str = "https://portkey.syngenta.com/v1"
    model_name: str = "@bedrock-aifoundry-euc1-001/global.anthropic.claude-opus-4-6-v1"

    # GitHub
    github_token: str = ""

    # Pipeline
    max_review_iterations: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
