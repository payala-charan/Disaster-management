# Architecture

## 1. End-to-end chain (required flow)

Agents never call each other directly. Each one reads from and writes to the **shared workflow state**
(dashed arrows), and LangGraph decides who runs next (solid arrows). Persisted results hand off to the human
side of the system.

```mermaid
flowchart LR
    subgraph Sources["Emergency sources"]
        S1[Emergency call]:::src
        S2[Field team]:::src
        S3[IoT sensor]:::src
        S4[Government alert]:::src
        S5[Citizen report]:::src
    end

    S1 & S2 & S3 & S4 & S5 -->|POST /api/intake/reports| RAW[(raw_reports)]

    subgraph Graph["LangGraph workflow (one run)"]
        direction LR
        A1[1 Incident Intake<br/>LLM extraction + geocoding<br/>+ duplicate engine]
        A3[3 Weather Intelligence<br/>Open-Meteo / simulation]
        A4[4 Geospatial and Access<br/>road registry + OSRM + PostGIS]
        A5[5 Resource Management<br/>registry + assignments]
        A2[2 Situation Assessment<br/>priority engine]
        A6[6 Allocation and Planning<br/>allocation engine, plans A/B/C]
        A8[8 Reviewer / Critic<br/>deterministic checks]
        A7[7 Coordination<br/>pending actions, alerts]
        A1 --> A3 & A4 & A5
        A3 & A4 & A5 --> A2 --> A6 --> A8
        A8 -->|blocking issues, max 2x| A6
        A8 -->|passed| A7
    end

    STATE[["Shared workflow state<br/>incidents, weather, access,<br/>resources, assessments, plans,<br/>critic findings, alerts, trace"]]
    A1 -.writes.-> STATE
    A3 -.writes.-> STATE
    A4 -.writes.-> STATE
    A5 -.writes.-> STATE
    STATE -.reads.-> A2
    A2 -.writes.-> STATE
    STATE -.reads.-> A6
    A6 -.writes.-> STATE
    STATE -.reads.-> A8
    STATE -.reads.-> A7

    RAW --> A1
    A7 --> DB[(plans, actions = pending,<br/>alerts, agent trace)]
    DB --> H{{Human approval<br/>Approval Center}}
    H -->|approve: row locks +<br/>unique index| T[Action tracking<br/>reserved → deployed → returning]
    T -->|changed condition| R[Replanning service]
    R -->|new run| A1

    classDef src fill:#1e293b,stroke:#64748b,color:#e2e8f0
```

## 2. LangGraph graph (generated)

Generated with `python -m app.graph.workflow --mermaid docs/agent_graph.md`. The dotted edges out of
`reviewer_critic` are the conditional route: back to allocation, or on to coordination.

```mermaid
graph TD;
	__start__([start]):::first
	incident_intake(incident_intake)
	weather_intelligence(weather_intelligence)
	geospatial_accessibility(geospatial_accessibility)
	resource_management(resource_management)
	situation_assessment(situation_assessment)
	resource_allocation(resource_allocation)
	reviewer_critic(reviewer_critic)
	emergency_coordination(emergency_coordination)
	__end__([end: pending actions await a coordinator]):::last
	__start__ --> incident_intake;
	incident_intake --> weather_intelligence;
	incident_intake --> geospatial_accessibility;
	incident_intake --> resource_management;
	weather_intelligence --> situation_assessment;
	geospatial_accessibility --> situation_assessment;
	resource_management --> situation_assessment;
	situation_assessment --> resource_allocation;
	resource_allocation --> reviewer_critic;
	reviewer_critic -.-> resource_allocation;
	reviewer_critic -.-> emergency_coordination;
	emergency_coordination --> __end__;
```

**Why severity runs after the fan-out:** the priority score uses weather (escalation factor), road access
(access factor) and current assignments (response gap). Running it after those three agents finish is
the simplest ordering in which every score uses this run's data.

## 3. Dynamic replanning loop

```mermaid
sequenceDiagram
    autonumber
    participant Src as Field update / simulation
    participant API as FastAPI service
    participant RP as Replanning service
    participant Eng as Impact engine (pure)
    participant DB as PostgreSQL
    participant G as Agent workflow
    participant UI as Browser (WebSocket)

    Src->>API: PATCH /api/roads/RD-B {status: closed, provenance: known}
    API->>DB: update road registry
    API->>RP: handle_change(road RD-B open→closed)
    RP->>DB: insert condition_changes row
    RP->>Eng: analyze_change(change, incidents, open actions)
    Eng-->>RP: affected incidents, pending to supersede, approved to review
    RP->>DB: pending actions → superseded (+ audit event)
    RP->>DB: approved/in-progress → "coordinator review required" note
    API->>DB: route_blocked alert (critical)
    API-->>UI: alert + situation_changed
    API->>G: background run (trigger road_change)
    G->>DB: new assessments, plans A/B/C, new pending actions
    G-->>UI: run_finished + situation_changed → pages refresh
```

## 4. Human approval and double-allocation protection

```mermaid
sequenceDiagram
    participant C1 as Coordinator 1
    participant C2 as Coordinator 2
    participant API as Approval service
    participant DB as PostgreSQL

    C1->>API: POST /actions/ACT-002/approve
    C2->>API: POST /actions/ACT-004/approve (shares BOAT-005)
    API->>DB: SELECT resources WHERE id IN (...) ORDER BY id FOR UPDATE
    Note over API,DB: C2's transaction waits on the row lock
    API->>DB: BOAT-005 available → reserved, assignment(reserved)
    API-->>C1: 200 approved
    API->>DB: (C2) re-check: BOAT-005 is reserved
    API->>DB: ACT-004 → superseded (audit event)
    API-->>C2: 409 "BOAT-005 is reserved (assigned to INC-003)"
    API->>API: background re-plan
    Note over DB: Partial unique index on resource_assignments(resource_id)<br/>WHERE status IN ('reserved','deployed') is the final guard
```

## 5. Deployment

```mermaid
flowchart LR
    U[Browser] -->|HTTPS| V[Vercel: React SPA<br/>VITE_API_URL]
    U -->|REST + WebSocket /ws| R[Render web service<br/>FastAPI + LangGraph]
    R --> P[(Render PostgreSQL + PostGIS)]
    R -->|optional| O[OpenAI API]
    R --> M[Open-Meteo]
    R --> S[OSRM public server]
```
