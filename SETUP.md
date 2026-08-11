# Chuẩn bị môi trường

## Yêu cầu

- Python 3.11 trở lên.
- Git.
- Tài khoản hoặc project Langfuse do Lab Coach cung cấp.
- Docker Desktop chỉ cần khi tự chọn chạy Langfuse local.

## 1. Tạo virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## 2. Cấu hình Langfuse — mặc định dùng chung/cloud

Ưu tiên project dùng chung do Lab Coach cung cấp hoặc Langfuse Cloud. Điền host và key của project vào `.env`:

```dotenv
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PROMPT_NAME=day13-chat
LANGFUSE_PROMPT_LABEL=production
```

Không commit `.env`. Nếu chưa có key, app vẫn chạy bằng prompt local; bạn vẫn làm được log, metrics và public tests nhưng chưa có evidence trace/prompt version.

## 3. Cấu hình OpenTelemetry — mặc định xuất console

OTEL được dùng song song với Langfuse để tạo span chuẩn cho request, RAG và LLM. Mặc định span được in ra console khi chạy local:

```dotenv
OTEL_SERVICE_NAME=day13-observability-lab
OTEL_TRACES_EXPORTER=console
OTEL_EXPORTER_OTLP_ENDPOINT=
```

### 3.1 Jaeger local qua OTLP

Cháº¡y Jaeger all-in-one Ä‘á»ƒ xem waterfall OTEL:

```bash
docker compose -f docker-compose.jaeger.yml up -d
```

Trong `.env` local, dùng OTLP HTTP:

```dotenv
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

Mở `http://localhost:16686`, chọn service `day13-observability-lab` và
kiểm tra các span `http.request`, `rag.retrieve`, `llm.generate`. Kết thúc
thì chạy `docker compose -f docker-compose.jaeger.yml down`.

Nếu nhóm có OTLP Collector, đổi exporter và điền endpoint:

```dotenv
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

Không ghi API key hoặc PII vào span attributes. Langfuse vẫn là nơi lưu prompt version, generation, token và cost; OTEL không thay thế phần đó.

## 4. Tùy chọn: chạy Langfuse local bằng Docker Compose

Phần này không bắt buộc và không được cộng điểm riêng. Chỉ dùng khi nhóm không truy cập được project chung/cloud và máy có Docker Desktop đủ tài nguyên.

Ở một thư mục nằm ngoài repo bài nộp:

```bash
git clone https://github.com/langfuse/langfuse.git langfuse-local
cd langfuse-local
docker compose up -d
```

Chờ container `langfuse-web` sẵn sàng, sau đó mở `http://localhost:3000`, tạo project và lấy public/secret key. Trong repo lab, đặt:

```dotenv
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Khi kết thúc buổi lab, dừng stack từ thư mục `langfuse-local`:

```bash
docker compose down
```

Không dùng `docker compose down -v` nếu còn cần dữ liệu trace/prompt trong volume. Xem hướng dẫn cập nhật tại [Langfuse Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose).

## 5. Kiểm tra cài đặt

Terminal 1:

```bash
uvicorn app.main:app --reload --env-file .env
```

Terminal 2:

```bash
python scripts/load_test.py
python scripts/validate_logs.py
python scripts/validate_dashboard.py
python -m pytest -q
```

API mặc định chạy tại `http://127.0.0.1:8000`; health check ở `/health`, metrics ở `/metrics`.

## Lỗi thường gặp

- `ModuleNotFoundError`: kiểm tra virtual environment đã được activate và chạy lại `pip install -r requirements.txt`.
- Không có `data/logs.jsonl`: bảo đảm API đang chạy trước khi chạy load test.
- Không thấy trace: kiểm tra ba biến `LANGFUSE_*`, sau đó khởi động lại API.
- Trace ghi `prompt_source=local-fallback`: kiểm tra host/key và prompt name/label trong `.env`.
- Docker local không lên: chạy `docker compose ps`, kiểm tra Docker Desktop và tài nguyên máy; có thể quay về project chung/cloud.
- Challenge chưa chạy: chờ Lab Coach release `config/challenge.json`.
## 6. Deploy Railway

`railway.toml` khai báo Nixpacks, health check `/health` và start command
nhận `PORT` do Railway cấp. Link/deploy sau khi đăng nhập bằng project token:

```bash
railway link --project <project-id> --environment production
railway up --project <project-id> --environment production
```

Khai báo `LANGFUSE_*`, `OTEL_*`, `APP_*` và `LOG_*` trong Railway Variables;
không commit `.env` hoặc `RAILWAY_TOKEN`. Trên Railway không dùng OTLP
`localhost`; chọn endpoint collector công khai, hoặc đặt
`OTEL_TRACES_EXPORTER=none`/`console` nếu chỉ xem Langfuse.

Sau khi deploy, cùng một domain Railway cung cấp các điểm truy cập sau:

- `/dashboard` — dashboard sáu nhóm chỉ số.
- `/health` — trạng thái ứng dụng, Langfuse và OTEL.
- `/metrics` — snapshot metrics dạng JSON.
- `/chat` — API tạo câu trả lời, log, metrics và traces.
