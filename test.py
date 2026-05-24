import json

import requests
from dotenv import dotenv_values

config = dotenv_values(".env")
print("🔍 Loaded environment variables")

# Validate environment variables
# Ensure all config values are strings
for key in config:
    if config[key] is not None:
        config[key] = str(config[key])

# Validate environment variables
required_keys = [
    "OPENROUTER_API_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_MODEL"
    ]
for key in required_keys:
    if key not in config or not config[key]:
        print(f"❌ Missing or empty environment variable: {key}")
        exit(1)

print("🔌 Connecting to API...")
try:
    # Ensure API URL is valid
    api_url = config["OPENROUTER_API_URL"]
    if not isinstance(api_url, str) or not api_url.startswith("http"):
        print(f"❌ Invalid API URL: {api_url}")
        exit(1)

    print(f"🌐 Sending request to {api_url}")

    try:
        response = requests.post(
            url=config["OPENROUTER_API_URL"],
            headers={
                "Authorization": f"Bearer {config['OPENROUTER_API_KEY']}",
            },
            data=json.dumps(
                {
                    "model": config["OPENROUTER_API_MODEL"],
                    "messages": [
                        {
                            "role": "user",
                            "content":
                                "How many r's are in the word 'strawberry'?",
                        }
                    ],
                    "reasoning": {"enabled": True},
                }
            ),
            timeout=10,  # Add timeout to prevent hanging
        )
        print(f"✅ Received API response (status: {response.status_code})")
    except requests.exceptions.Timeout:
        print("❌ API request timed out after 10 seconds")
        exit(1)

    # Check for HTTP errors
    response.raise_for_status()

    print("📊 Parsing JSON response...")
except requests.exceptions.RequestException as e:
    print(f"❌ API request failed: {e}")
    if hasattr(e, "response") and e.response:
        print(f"🔍 Response content: {e.response.text[:200]}")
    exit(1)

# Extract the assistant message with reasoning_details
try:
    print("📊 Parsing JSON response...")
    response_data = response.json()
    response_message = response_data["choices"][0]["message"]
    print("✅ Successfully parsed response")
except json.JSONDecodeError as e:
    print(f"❌ Failed to parse JSON response: {e}")
    print(f"🔍 Response content: {response.text[:200]}")
    exit(1)

print("📄 Response content:")
print(json.dumps(response_message, indent=2))
# Preserve the assistant message with reasoning_details
messages = [
    {
        "role": "user",
        "content": "How many r's are in the word 'strawberry'?"
        },
    {
        "role": "assistant",
        "content": response_message.get("content"),
        "reasoning_details": response_message.get(
            "reasoning_details"
        ),  # Pass back unmodified
    },
    {"role": "user", "content": "Are you sure? Think carefully."},
]
