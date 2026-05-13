# Gemini API Key Setup

Use this path when you do not want to set up Google Cloud Project ID, `gcloud`,
or Vertex AI authentication.

## 1. Get An API Key

Go to Google AI Studio:

https://aistudio.google.com/app/apikey

Create or copy a Gemini API key.

Official Google docs: https://ai.google.dev/gemini-api/docs/api-key

## 2. Install The SDK

Run this inside the project folder:

```bat
cd /d "C:\Users\iy579\Documents\New project 2\exam-agent-project"
python -m pip install google-genai
```

## 3. Set The API Key In cmd

For Windows cmd:

```bat
set GEMINI_API_KEY=PASTE_YOUR_KEY_HERE
```

Check that it is set:

```bat
echo %GEMINI_API_KEY%
```

Do not paste the key into GitHub, README files, screenshots, or code.

## 4. Run The Final Gemini Pipeline

Low-cost final run:

```bat
python .\src\main.py --provider gemini --quality final_low_cost --strict-provider
python .\src\evaluation.py --provider gemini --quality final_low_cost --strict-provider --simulate-trials 1
```

High-quality final run:

```bat
python .\src\main.py --provider gemini --quality final --strict-provider
python .\src\evaluation.py --provider gemini --quality final --strict-provider --simulate-trials 1
```

`final` tries higher-quality models such as `gemini-2.5-pro` first. If quota is
exhausted, the provider now retries lower-cost models such as
`gemini-2.5-flash` and `gemini-2.5-flash-lite` before failing. If your account
has no Pro quota, use `final_low_cost` directly.

If `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set, the provider now uses the simple
Google AI Studio API-key path. If neither key is set, it falls back to the older
Vertex AI path that requires `GCP_PROJECT_ID` and `gcloud`.
