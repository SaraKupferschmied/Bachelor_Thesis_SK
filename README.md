# Semester Planning Chatbot

This repository contains the implementation developed as part of a bachelor thesis on **Structured Data for Reliable Chatbots**.

The project provides a locally hosted chatbot system designed to help university students plan their studies and upcoming semesters at the UniFr. The chatbot combines:

- Structured backend APIs
- Tool-based chatbot architecture
- Retrieval-Augmented Generation (RAG)
- Vector databases
- Relational data storage

to provide reliable information about university courses, including:

- Course schedules and timeslots
- Study program information
- Course descriptions and metadata
- Semester planning support

In addition to the chatbot interface, the project also includes a **semester planner UI**, which directly communicates with backend services to visualize and organize semester plans.

---

# Features

- AI-powered chatbot for semester planning
- Tool-based architecture for improved reliability
- Structured course and study program data
- RAG-based semantic retrieval
- Semester visualization and planning UI
- Dockerized multi-service setup
- Swagger documentation

---

# System Architecture

The system consists of multiple services running in Docker containers:

| Service | Description | Default Port |
|---|---|---|
| Frontend | Angular-based user interface | `4200` |
| Backend API | Main application backend | `3000` |
| Chatbot Service | AI chatbot & RAG pipeline | `8000` |

Swagger/OpenAPI documentation is available at:

- `http://localhost:3000/docs`
- `http://localhost:8000/docs`

---

# Technologies Used

- Angular
- FastAPI
- PostgreSQL
- Docker & Docker Compose
- Vector Databases
- Retrieval-Augmented Generation (RAG)
- Tool-based LLM Architecture

---

# Run Locally

## Requirements

Before starting, make sure the following tools are installed:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Git](https://git-scm.com/)

---

# Setup

## 1. Clone the Repository

```bash
git clone https://github.com/SaraKupferschmied/semester-planning-chatbot
cd semester-planning-chatbot
```

---

## 2. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

Then edit the `.env` file and provide your own configuration values.

> Note: Sensitive environment variables are intentionally not included in the repository for security.

---

## 3. Start the Application

For the initial startup, run:

```bash
docker compose --profile jobs up --build
```

The initial startup may take up to 30 minutes because:

- Docker images are built
- Ollama models are downloaded 
- Project dependencies are installed
- The database is initialized and seeded

> Note: Subsequent startups are significantly faster and usually only take a few minutes.

---

## 4. Subsequent Runs

After the initial setup, the application can be started with:

```bash
docker compose up
```

---

# Access the Application

Once all services are running, the application can be accessed by opening http://localhost:4200 in a browser of your choice.

---

# Research Context

This project was developed in the context of a bachelor thesis investigating how **structured data and tool-based architectures can improve chatbot reliability** in educational planning systems.

The focus of the work is on reducing hallucinations and improving response accuracy by combining:

- Structured APIs
- Deterministic tool usage
- Semantic retrieval
- Database-backed information systems

The system explores how LLM-based assistants can provide more trustworthy and reliable responses when grounded in structured educational data sources.

---

# Future Improvements

Potential future extensions include:

- Integration with real university systems
- Reduced Latency for RAG flows
- Authentication and personalized study plans
- Multi-university support
- Advanced recommendation systems
