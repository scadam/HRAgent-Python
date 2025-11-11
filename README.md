
# HR Agent for Workday (Python on Azure Functions)

This repository contains the HR Agent Workday integration that powers Microsoft 365 Copilot scenarios. The backend is implemented as a Python Azure Functions Flex Consumption app that proxies Workday REST APIs using the caller-provided OAuth token, and the Microsoft 365 Agents Toolkit project scaffolds provisioning, deployment, and local testing.

---

## Prerequisites

- **Azure subscription** with permission to create resource groups, storage accounts, and Flex Consumption Function Apps.
- **Microsoft 365 tenant** where you can sideload Copilot agents and acquire OAuth registrations.
- **Workday sandbox** (or equivalent) and a service principal with access to required reports and APIs.
- **Git** 2.40+ for cloning the repository.
- **Node.js** 18 LTS (required by the Microsoft 365 Agents Toolkit tooling).
- **Python** 3.10.x (the runtime used by the Flex Consumption function app).
- **Visual Studio Code** with the Microsoft 365 Agents Toolkit extension.
- **Microsoft 365 Agents Toolkit CLI** (`npm install -g @microsoft/m365agentstoolkit-cli`, command `atk`).
- **Azure Functions Core Tools** v4 (installed automatically by the deploy workflow, but installing the MSI locally enables direct `func` commands).

> Verify your toolchain: `git --version`, `node --version`, `npm --version`, `python --version`, `atk --version`, `func --version`.

---

## 1. Clone and Open the Project

```powershell
git clone https://github.com/scadam/HRAgent-Python.git
cd HRAgent-Python
code .
```

On first launch VS Code prompts you to install recommended extensions—accept the Microsoft 365 Agents Toolkit recommendation.

---

## 2. Bootstrap Python Environment

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS/Linux activation command:

```bash
source .venv/bin/activate
```

---

## 3. Configure Microsoft 365 Agents Toolkit Environment Files

The Microsoft 365 Agents Toolkit reads configuration from `env/.env.<environment>` files. For a new `dev` environment:

1. Copy the template below into `env/.env.dev` (overwrite the placeholder values).
2. Store secrets that you do not want committed in `env/.env.dev.user` (this file is ignored by Git).

```dotenv
# env/.env.dev
TEAMSFX_ENV=dev
APP_NAME_SUFFIX=<short suffix such as dev01>

AZURE_SUBSCRIPTION_ID=<subscription GUID>
AZURE_RESOURCE_GROUP_NAME=<resource group to create, e.g. rg-hragent-dev>
AZURE_LOCATION=<Azure region, e.g. uksouth>
AZURE_FUNCTION_BASE_NAME=<globally unique base, used for storage + function app>
AZURE_FUNCTION_SKU=FC1
AZURE_FUNCTION_INSTANCE_MEMORY_MB=512
AZURE_FUNCTION_MAXIMUM_INSTANCE_COUNT=40
AUTHCODE_CONFIGURATION_ID=

# Values below are populated automatically after provisioning/deployment.
TEAMS_APP_ID=
M365_TITLE_ID=
M365_APP_ID=
TEAMS_APP_TENANT_ID=
AAD_APP_CLIENT_ID=
AAD_APP_OBJECT_ID=
AAD_APP_TENANT_ID=
AAD_APP_OAUTH_AUTHORITY=
AAD_APP_OAUTH_AUTHORITY_HOST=https://login.microsoftonline.com
RESOURCE_SUFFIX=
FUNC_ENDPOINT=
API_FUNCTION_ENDPOINT=
API_FUNCTION_RESOURCE_ID=
OPENAPI_SERVER_URL=
OPENAPI_SERVER_DOMAIN=
FUNCTION_APP_NAME=
FUNC_PATH=
```

- `AZURE_FUNCTION_BASE_NAME` must be alphanumeric, 3–11 characters, and unique per tenant because it seeds the storage account and function app names.
- Leave the lower section blank; `atk provision` and `atk deploy` will populate it.
- Set any secrets (for example, Workday credentials if ever required) in `.env.dev.user` to keep them out of source control.

If you also plan to run locally, repeat the process for `env/.env.local` with `TEAMSFX_ENV=local` and your preferred `APP_NAME_SUFFIX`.

---

## 4. Provide Copilot OAuth Reference

Before provisioning you need an OAuth client stored in the Teams (Copilot) plugin vault. Create the client manually in the Developer Portal and set its reference ID in your environment file:

1. In the Teams Developer Portal (aka.ms/m365-dev-portal) browse to **Settings → OAuth connections** and create/configure the Workday OAuth client if it does not already exist.
2. Copy the `Reference ID` shown for that connection.
3. Open `env/.env.dev` and set `AUTHCODE_CONFIGURATION_ID=<reference ID>`, keeping the other values unchanged.

The plugin manifest already references this environment variable:

```json
"auth": {
    "type": "OAuthPluginVault",
    "reference_id": "${{AUTHCODE_CONFIGURATION_ID}}"
}
```

As long as the manifest keeps that placeholder, the packaging step will pull in the value from `.env.dev`. Update the environment variable again if you ever rotate or recreate the OAuth connection.

---

## 5. Update Local Workday Settings

Edit `local.settings.json` to match your Workday sandbox URLs and tenant identifiers.

```jsonc
{
    "IsEncrypted": false,
    "Values": {
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsStorage": "UseDevelopmentStorage=true",
        "WORKDAY_BASE_URL": "https://<tenant>.workday.com",
        "WORKDAY_TENANT": "<tenant short name>",
        "WORKDAY_WORKER_SEARCH_URL": "https://.../COPILOT_CURRENTUSER",
        "WORKDAY_WORKERS_API_URL": "https://.../workers",
        "WORKDAY_ABSENCE_API_BASE": "https://.../absenceManagement/v1/<tenant>",
        "WORKDAY_COMMON_API_BASE": "https://.../common/v1/<tenant>",
        "WORKDAY_LEARNING_API_BASE": "https://.../learning/v1/<tenant>",
        "WORKDAY_LEARNING_ASSIGNMENTS_REPORT_URL": "https://.../Required_Learning?format=json",
        "WORKDAY_HTTP_TIMEOUT": "30"
    }
}
```

- These values are consumed both locally and in Azure; the deploy process copies them into app settings.
- For local development with storage emulator, install [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) or update `AzureWebJobsStorage` to an Azure Storage connection string.

---

## 6. Sign In to Required Clouds

The CLI uses account contexts per environment:

```powershell
atk account login azure
atk account login m365
```

Confirm you are targeting the correct subscription and tenant via `atk account status`.

---

## 7. Provision Azure and Microsoft 365 Resources

Provisioning creates the Flex Consumption function app, storage account, required role assignments, Teams/Outlook agent registration, and OAuth configuration. Run:

```powershell
atk provision --env dev
```

Provision outputs are written back into `env/.env.dev`. Review the Azure portal resource group (`AZURE_RESOURCE_GROUP_NAME`) to confirm the deployment succeeded.

---

## 8. Deploy the Function App

Deployment uses Azure Functions Core Tools remote build tailored for Flex Consumption:

```powershell
atk deploy --env dev
```

The script in `scripts/publish-function.ps1` publishes the contents of `src/` to the provisioned Function App and runs `sync triggers`. When complete, the CLI prints all HTTP trigger URLs.

Validate deployment with a simple ping (replace the URL with the value from the output):

```powershell
invoke-webrequest https://<function-app-name>.azurewebsites.net/api/gettimeoffentries -UseBasicParsing -Headers @{ Authorization = "Bearer <workday token>" }
```

---

## 9. Run Locally (Optional)

To emulate the experience locally with a public tunnel:

1. In VS Code, run the `Start Agent Locally` task (from the Terminal > Run Task menu). This installs dependencies, provisions local resources, starts a dev tunnel, builds the agent, and launches the function host.
2. Alternatively, run the host manually:

     ```powershell
     func start --python --port 7071 --cors "*" --script-root src
     ```

3. Provide Workday OAuth tokens via the `Authorization` header when calling the HTTP endpoints.

---

## 10. Troubleshooting Tips

- Run `atk env list` to confirm the correct environment is active.
- Use `atk deploy --env dev --verbose` to surface detailed Core Tools logs if functions are missing after publish.
- Tail live Azure logs with `func azure functionapp log tail <function-app-name>`.
- Ensure your Workday token has the reports/api scopes referenced in `shared/workdayHelpers.js`.

---

With these steps a new contributor can clone the repository, configure environment files, provision Azure/Microsoft 365 assets, and deploy the Workday HR Agent Python function app end-to-end.

