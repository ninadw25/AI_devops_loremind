# Run Lore locally (course demo)

Needs: Docker, `NVIDIA_API_KEY` and `BEDROCK_API_KEY` in `.env`, the Neo4j volume `lore-ingestion_neo4j_data`.

```
cd lore-course/lore-engine
scripts/course_up.sh            # or: scripts/course_up.sh docker   (runs the same image EKS runs)
```
Check: `curl localhost:8000/api/health` → neo4j/memory_postgres `up`, embeddings `nemotron-3-embed-1b`.

Ask a question (the engine trusts a gateway-forwarded token; locally mint one):
```
S=$(grep ^INTERNAL_AUTH_SECRET= .env | cut -d= -f2-)
T=$(python -c 'import jwt,time;print(jwt.encode({"sub":"demo","org":"ezobooks","exp":int(time.time())+9999},"x"*32,algorithm="HS256"))')
curl -s localhost:8000/api/query -H "X-Internal-Auth: $S" -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"query":"Who handles hiring?","k":8,"super_admin":true}'
```
Show the graph: Neo4j Browser http://localhost:7474 —
`MATCH (p:Person {name:"<name>"})<-[:ASSIGNED_TO]-(t:Task)-[:REGARDING]->(x:Topic) RETURN p,t,x LIMIT 25`

Evaluation: see docs/MIDTERM_REPORT.md §8. Tests: `pytest tests -q` (307 pass).

## If the machine was wiped
1. `docker volume ls` must show `lore-ingestion_neo4j_data` (else restore `lore-ingestion/neo4j_data.tar.gz`).
2. Start Neo4j on it: `docker run -d --name lore-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<pw> -v lore-ingestion_neo4j_data:/data neo4j:5.26-community`.
3. Memory DB: `docker compose up -d` in this folder (pgvector, port 5433).
4. NVIDIA retired `llama-nemotron-embed-1b-v2`; vectors in the graph must be `nemotron-3-embed-1b`:
   `python -m runners.reembed_graph --org ezobooks` (lore-ingestion, ~25 min), then
   `python -m runners.backfill_gmail_scopes --org ezobooks --events <gmail_filtered.jsonl>` for graphs built before scopes existed.
