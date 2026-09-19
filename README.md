# SignalLens

SignalLens is a research dashboard for periodically ranking public companies and tracking each ranking against what happened afterward. It is decision support, not financial advice.

## Milestone 1

- Next.js and TypeScript dashboard
- FastAPI service with health and demo-ranking endpoints
- Automated backend tests and frontend production build
- One-command Windows development startup
- Written status and roadmap

## Requirements

- Windows PowerShell 5.1+
- Python 3.12+
- Node.js 20+
- Git

## Install at the requested location

Extract or clone this repository to:

```text
C:\Users\Juan Estrada\Projects\SignalLens
```

## Start the application

Open PowerShell in the project folder and run:

```powershell
.\start.ps1
```

On the first run, the script creates a Python virtual environment, installs backend and frontend dependencies, copies `.env.example` to `.env` when needed, and opens two process windows.

- Dashboard: http://localhost:3000
- Backend health: http://127.0.0.1:8000/api/v1/health
- API documentation: http://127.0.0.1:8000/docs

Stop the two development-server windows with `Ctrl+C`.

## Manual verification

```powershell
.\scripts\test.ps1
```

## Project layout

```text
SignalLens/
├── backend/             FastAPI application and tests
├── frontend/            Next.js application
├── scripts/             Windows development and test scripts
├── .env.example         Safe configuration template
├── README.md            Setup and usage
├── ROADMAP.md           Planned milestones
└── STATUS.md            Current verified state
```

## Important

The three companies currently shown are deterministic demo records. They are not recommendations and are not produced by a trained SignalLens model.

