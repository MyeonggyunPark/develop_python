# AI Agent Workspace

`platform`은 FastAPI API 서버와 React 프론트를 분리한 구조로 정리 중입니다.

## Backend

FastAPI 백엔드는 `platform/app`에 있고, 현재는 API 서버 역할만 담당합니다.

```bash
cd platform
uvicorn app.main:app --reload
```

기본 주소는 `http://127.0.0.1:8000`입니다.

## Frontend

React 프론트는 `platform/frontend`에 있습니다.

```bash
cd platform/frontend
npm install
npm run dev
```

기본 개발 서버는 `http://127.0.0.1:5173`이고, Vite proxy로 FastAPI의 `/api`, `/health`를 전달합니다.

별도 백엔드 주소를 직접 지정하려면 아래 환경 변수를 사용하면 됩니다.

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```
