import { defineRailway, github, image, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const signallensVolume = volume("signallens-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 10000 });
  const SignalLens = service("SignalLens", {
    source: github("Juanma197/SignalLens", { checkSuites: false, rootDirectory: "/backend" }),
    // From backend/railway.toml (Config as Code, retired 2026-12-01). Restarts on failure: Railway's default policy, stored as unset.
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    deploy: { healthcheckPath: "/api/v1/health", healthcheckTimeout: 300, restartPolicyMaxRetries: 5 },
    start: "sh -c 'uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\"'",
    replicas: { "sfo": 1 },
    networking: { privateNetworkEndpoint: "signallens" },
    volumeMounts: { "/data": signallensVolume },
    env: { SIGNALLENS_ALERTS_ENABLED: preserve(), SIGNALLENS_ALERTS_UTC_TIME: preserve(), SIGNALLENS_ALLOWED_ORIGINS: preserve(), SIGNALLENS_API_HOST: preserve(), SIGNALLENS_API_PORT: preserve(), SIGNALLENS_API_TOKEN: preserve(), SIGNALLENS_BACKUP_MAX_AGE_HOURS: preserve(), SIGNALLENS_BACKUP_PATH: preserve(), SIGNALLENS_BACKUP_RETENTION_COUNT: preserve(), SIGNALLENS_DATABASE_PATH: preserve(), SIGNALLENS_ENVIRONMENT: preserve(), SIGNALLENS_EODHD_API_TOKEN: preserve(), SIGNALLENS_FRED_API_KEY: preserve(), SIGNALLENS_PERSISTENT_VOLUME_PATH: preserve(), SIGNALLENS_PROTOTYPE_DATABASE_PATH: preserve(), SIGNALLENS_PROTOTYPE_WRITES_ENABLED: preserve(), SIGNALLENS_PUBLIC_URL: preserve(), SIGNALLENS_RESEARCH_DATABASE_PATH: preserve(), SIGNALLENS_SCHEDULER_ENABLED: preserve(), SIGNALLENS_SEC_USER_AGENT: preserve(), SIGNALLENS_STAGING_MODE: preserve(), SIGNALLENS_TELEGRAM_BOT_TOKEN: preserve(), SIGNALLENS_TELEGRAM_CHAT_ID: preserve() },
  });
  const worthyPatience = service("worthy-patience", {
    source: github("Juanma197/SignalLens", { checkSuites: false, rootDirectory: "/frontend" }),
    // From frontend/railway.toml (Config as Code, retired 2026-12-01). Restarts on failure: Railway's default policy, stored as unset.
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    deploy: { healthcheckPath: "/api/health", healthcheckTimeout: 300, restartPolicyMaxRetries: 5 },  // "/" requires the dashboard login
    replicas: { "sfo": 1 },
    env: { SIGNALLENS_API_TOKEN: preserve(), SIGNALLENS_API_URL: preserve(), SIGNALLENS_DASHBOARD_PASSWORD: preserve(), SIGNALLENS_DASHBOARD_USERNAME: preserve(), SIGNALLENS_ENVIRONMENT: preserve() },
  });
  const signallensMonthlyScheduler = service("signallens-monthly-scheduler", {
    source: image("curlimages/curl:8.12.1"),
    start: "/bin/sh -c 'exec curl --fail-with-body --silent --show-error --connect-timeout 30 --max-time 3600 --request POST --header \"Authorization: Bearer $SIGNALLENS_API_TOKEN\" \"$SIGNALLENS_MONTHLY_CYCLE_URL\"'",
    replicas: { "sfo": 1 },
    env: { SIGNALLENS_API_TOKEN: preserve(), SIGNALLENS_MONTHLY_CYCLE_URL: preserve() },
  });

  return project("respectful-exploration", {
    resources: [SignalLens, worthyPatience, signallensMonthlyScheduler, signallensVolume],
  });
});
