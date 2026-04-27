import Fastify from "fastify";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import dotenv from "dotenv";
import cors from "@fastify/cors";

dotenv.config({ path: ".env.docker" });

import { programsRoutes } from "./routes/programs.js";
import { coursesRoutes } from "./routes/courses.js";
import { offeringsRoutes } from "./routes/offerings.js";
import { docsRoutes } from "./routes/docs.js";
import { plannerRoutes } from "./routes/planner.js";
import { studyProgramPlannerRoutes } from "./routes/studyProgramPlanner.js";
import { pool } from "./db.js";

async function main() {
  const app = Fastify({ logger: true });

  // cors
  await app.register(cors, {
    origin: process.env.CORS_ORIGIN ?? true,
  });

  // Swagger
  await app.register(swagger, {
    openapi: {
      info: {
        title: "Semester Planning API",
        description: "REST API for SQL-backed chatbot",
        version: "1.0.0",
      },
    },
  });

  await app.register(swaggerUi, { routePrefix: "/docs" });

  // Routes
  app.get("/health", async () => ({ ok: true }));
  
  app.register(programsRoutes, { prefix: "/programs" });
  app.register(coursesRoutes, { prefix: "/courses" });
  app.register(offeringsRoutes, { prefix: "/offerings" });
  app.register(docsRoutes, { prefix: "/docs-api" }); // avoid conflict with swagger /docs
  app.register(plannerRoutes, { prefix: "/planner" });
  app.register(studyProgramPlannerRoutes, { prefix: "/planner" });

  const port = Number(process.env.PORT ?? 3000);
  await app.listen({ port, host: "0.0.0.0" });

  // 🔹 Pretty startup logs
const address = `http://localhost:${port}`;

console.log("\n🚀 Server running at:", address);
console.log("📘 Swagger UI available at:", `${address}/docs\n`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});