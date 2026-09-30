# Running on the NPU / iGPU with OpenVINO Model Server (experimental)

The backend talks to its model server through an OpenAI-compatible API, so Ollama can be
swapped for **OpenVINO Model Server (OVMS)**, which can run models on the Core Ultra's NPU
or integrated GPU. This is a prototype path: the switch is built and unit-tested, but it
has not yet been benchmarked on the Vivobook. Measure before and after with `eval\run_eval.py`.

## 1. Export models for OVMS

Follow OVMS's `export_models` demo (github.com/openvinotoolkit/model_server,
`demos/common/export_models`). In a separate Python venv:

```powershell
# A small instruct model, int4, for the NPU (pick one listed as NPU-supported in the OVMS docs)
python export_model.py text_generation --source_model <hf-model-id> --weight-format int4 --target_device NPU --config_file_path models\config.json --model_repository_path models
# An embedding model (CPU or GPU is fine; the NPU needs a fixed --max_length)
python export_model.py embeddings_ov --source_model <hf-embedding-id> --pooling CLS --config_file_path models\config.json --model_repository_path models
```

## 2. Start OVMS

```powershell
ovms --rest_port 8000 --config_path models\config.json
```

OVMS serves `http://localhost:8000/v3/chat/completions` and `/v3/embeddings`.

## 3. Point the app at it

In `backend\.env`:

```
LLM_PROVIDER=openai
OLLAMA_BASE_URL=http://localhost:8000/v3
OLLAMA_MODEL=<the model name from config.json>
EMBEDDING_MODEL=<the embedding model name from config.json>
```

Then restart the app and **rescan** your folders: vectors from a different embedding
model aren't comparable with the old ones. (Remove the folder and add it again, or delete
the `lancedb` folder in `%APPDATA%\LocalFileAssistant`.)

## What changes with `LLM_PROVIDER=openai`

- Chat: same OpenAI client, different base URL.
- Embeddings: `POST {OLLAMA_BASE_URL}/embeddings` instead of Ollama's `/api/embed`.
- Model list / connection status: `GET {OLLAMA_BASE_URL}/models`. If your OVMS version
  doesn't serve that, Settings shows "not running" even though answers work.
- Idle unloading (`LLM_IDLE_UNLOAD_S`) only applies to Ollama; OVMS keeps models loaded.
- Qwen3 models may need thinking turned off (`chat_template_kwargs: {"enable_thinking": false}`)
  for short, direct answers; not wired up yet.
