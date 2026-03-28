import dotenv from "dotenv";
import fs from "fs";
import path from "path";

// Load env file ONLY if it exists and if POSTGRES_USER isn't already provided.
// This works for both local dev and Docker.
function loadEnvIfNeeded() {
  if (process.env.POSTGRES_USER) return;

  const candidates = [
    path.resolve(process.cwd(), ".env.docker"),
    path.resolve(process.cwd(), ".env.local"),
    
    // fallback to your previous relative guess (repo root)
    path.resolve(__dirname, "../../../.env.docker"),
    path.resolve(__dirname, "../../../.env.local"),
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) {
      dotenv.config({ path: p });
      break;
    }
  }
}

loadEnvIfNeeded();

function requireEnv(name: string): string {
  const value = process.env[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const environment = {
  db: {
    user: requireEnv("POSTGRES_USER"),
    host: process.env.POSTGRES_HOST ?? "localhost",
    name: requireEnv("POSTGRES_DB"),
    password: requireEnv("POSTGRES_PASSWORD"),
    port: Number(process.env.POSTGRES_PORT ?? "5432"),
  },
};