# Vertex AI / Agent Platform API Setup

This is the setup path used in the M5.3.1.1 lecture notebook. It uses Google
Cloud credits through Vertex AI, not a Google AI Studio API key.

## 1. Enable The Lecture API

In Google Cloud Console:

1. Open the project used for the class.
2. Go to **APIs & Services** -> **Library**.
3. Search for **Agent Platform API**.
4. Enable it.

If the page shows **Vertex AI API** or **AI Platform API**, that is fine as long
as the service name is `aiplatform.googleapis.com`.

## 2. Copy The Project ID

Use the **Project ID**, not the display name.

Good examples look like:

```text
scientific-management-494205
exam-agent-project-123456
```

Display names with spaces are not enough.

## 3. Local Windows Setup

The lecture notebook uses:

```python
from google.colab import auth
auth.authenticate_user()
```

That only works inside Google Colab. On your Windows terminal, the equivalent is
Google Cloud CLI application-default login.

Install Google Cloud CLI first:

https://cloud.google.com/sdk/docs/install

Then open a new terminal.

For Windows cmd:

```bat
set GCP_PROJECT_ID=your-project-id
set GCP_LOCATION=us-central1
gcloud auth application-default login
```

For PowerShell:

```powershell
$env:GCP_PROJECT_ID="your-project-id"
$env:GCP_LOCATION="us-central1"
gcloud auth application-default login
```

If you want the project ID to persist in future terminals, use:

```bat
setx GCP_PROJECT_ID your-project-id
setx GCP_LOCATION us-central1
```

After `setx`, close and reopen the terminal.

## 4. Run The Project With The Lecture Path

Use `--provider vertex` to force the Vertex AI / Agent Platform API path:

```bat
cd /d "C:\Users\iy579\Documents\New project 2\exam-agent-project"
python .\src\main.py --provider vertex --quality final_low_cost --strict-provider
python .\src\evaluation.py --provider vertex --quality final_low_cost --strict-provider --simulate-trials 1
```

This maps to the lecture notebook pattern:

```python
client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
    http_options=HttpOptions(api_version="v1"),
)
```

## 5. Common Errors

`$env:GCP_PROJECT_ID=...` fails in cmd because that is PowerShell syntax. In cmd,
use `set GCP_PROJECT_ID=...`.

`'gcloud' is not recognized` means Google Cloud CLI is not installed, or the
terminal was opened before installation updated PATH.

`GCP_PROJECT_ID is not set` means the terminal running Python does not currently
have the project ID environment variable.

`PermissionDenied` usually means the selected Google account does not have
permission on that project, or the Agent Platform / Vertex AI API is not enabled.
