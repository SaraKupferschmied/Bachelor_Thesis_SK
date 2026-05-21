# BA_Thesis_Dev
This Repo is the development environment for the web crawling and DB population for my bachelor thesis. 

--------------Web crawling and DB creation------------------------------------------------------------------------------------

Start Docker and initiate the schema
1. .venv\Scripts\activate
2. cd DB_Service
3. npm install
4. cd ..
5. docker compose --env-file .env.docker up --build
6. npm run schema

Run the Spiders to create json data
1. cd scrapy_crawler\scrapy_crawler
2. scrapy crawl curricula_links_level2_ects -O spider_outputs\program_links_with_ects.json
2.1 scrapy crawl curricula_links_level2_enriched -O programmes_with_curricula_enriched.json
3. scrapy crawl download_links_level3 -O spider_outputs\download_links.json -a input_json_path=spider_outputs\program_links_with_ects.json
4. scrapy crawl faculty_links -O spider_outputs\faculties.json
5. scrapy crawl unifr_edu_studyplans -O spider_outputs\faculty_programs\edu.json
6. scrapy crawl unifr_scimed_studyplans -O spider_outputs\faculty_programs\scimed.json
7. scrapy crawl unifr_interfaculty_studyplans -O spider_outputs\faculty_programs\interfaculty.json
8. scrapy crawl unifr_ius_studyplans -O spider_outputs\faculty_programs\law.json
9. scrapy crawl unifr_phil_studyplans -O spider_outputs\faculty_programs\philo.json
10. scrapy crawl unifr_ses_studyplans -O spider_outputs\faculty_programs\ses.json
11. scrapy crawl unifr_theo_studyplans -O spider_outputs\faculty_programs\theo.json
12. scrapy crawl timetable_courses -O spider_outputs\courses.json
13. scrapy crawl unifr_directory -a courses_file=spider_outputs\courses.json -O spider_outputs\unifr_people.jsonl
14. scrapy crawl reglementation -O spider_outputs\reglementation_docs.json

Merge crawled docs
1. python DB_service\src\import\normalize_faculty_jsons.py ^  --input-dir scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs ^  --out scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs_normalized.json
 
2. python DB_service\src\import\merge_studyplans.py ^  --base scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects.json ^  --inputs scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs_normalized.json ^  --out scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs.json

3. python DB_service\src\import\unmatched_patch.py ^  --in "scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs.json" ^  --out "scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs_enriched.json"

Download and parse
1. npm i axios tough-cookie axios-cookiejar-support
2. npx ts-node DB_service\src\import\01_download_program_docs_v2.ts --input scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs_enriched.json --out scrapy_crawler/outputs
3. npx ts-node DB_service/src/import/parse_docs_full.ts --root scrapy_crawler/outputs
4. npx ts-node DB_service/src/import/reglementation_download_docs.ts   --input scrapy_crawler/scrapy_crawler/spider_outputs/reglementation_docs.json \  --out scrapy_crawler/outputs/reglementation_docs
5. npx ts-node DB_service/src/import/parse_reglementation_docs_full.ts --root scrapy_crawler/outputs/reglementation_docs


Do the imports (from root)
1. npx ts-node DB_sercive/src/import/run_faculty_import.ts
2. npx ts-node DB_service/src/import/run_courses_import.ts
3. npx ts-node DB_service/src/import/update_professors_from_people.ts
4. npx ts-node DB_service/src/import/program_name_imports.ts
4.1 npx ts-node DB_service/src/import/program_basedata_imports.ts
5. npx ts-node DB_service/src/import/new_program_import.ts
5.1 docker compose --env-file .env.docker --profile jobs run --rm import_data sh -lc "npx ts-node src/import/new_program_imports_dockling.ts"
5.1 docker compose --env-file .env.docker --profile jobs run --rm import_data sh -lc "npx ts-node src/import/prune_staging_to_current_program_documents.ts"
6. npx ts-node DB_service/src/import/import_consist_of.ts
7. npx ts-node DB_service/src/import/run_reglementation_import.ts --root scrapy_crawler/outputs/reglementation_docs

--------------BACKEND API------------------------------------------------------------------------------------
Start the server
- cd backend_api
- npm run dev
- (see swagger at http://localhost:3000/docs)

start chatbot from folder
- uvicorn app.main:app --reload

docker compose --env-file .env.docker restart chatbot
docker compose --env-file .env.docker logs -f chatbot

npx ts-node DB_service/src/import/parse_docs_full_docling.ts --root scrapy_crawler/outputs --docling-helper DB_service/src/import/parse_with_docling.py

python -m app.build_faiss_docling --target studyplans --parser docling --force
python -m app.build_faiss_docling --target regulations --parser docling --force

when already some exist: npx ts-node DB_service/src/import/parse_docs_full_docling.ts --root scrapy_crawler/outputs --docling-helper DB_service/src/import/parse_with_docling.py --append-index

npx tsx DB_service\src\import\validate_docling_metadata.ts --dir "C:\Users\Sara\OneDrive\Uni\Bachelor thesis\BA_Thesis_Dev\scrapy_crawler\outputs\parsed_fulltext_docling" --verbose


python scrapy_crawler\outputs\infer_metadata_staging.py scrapy_crawler\outputs\parsed_fulltext_docling\_index.jsonl --input-dir scrapy_crawler\outputs\parsed_fulltext_docling --staging-dir scrapy_crawler\outputs\metadata_staging

npx tsx DB_service\src\import\parse_docs_full_docling_new.ts --root scrapy_crawler/outputs --manifest scrapy_crawler/outputs/_program_docs_manifest.json --parsed-dir scrapy_crawler/outputs/parsed_fulltext_docling_new --docling-helper DB_service\src\import\parse_with_docling_robust.py --python python

python propose_program_metadata_corrections_manifest_scoped.py .

python scrapy_crawler\outputs\apply_program_metadata_to_parsed_files.py scrapy_crawler\outputs scrapy_crawler\outputs\program_metadata_correction_proposal.json

docker compose --env-file .env.docker --profile jobs run --rm import_data sh -lc "npx ts-node src/import/new_program_imports_dockling.ts"

set RAG_PARSER=docling_parent_child
python -m chatbot.app.build_faiss_docling_parent_child --target studyplans --force

set RAG_PARSER=docling_table_semantic
python -m chatbot.app.build_faiss_docling_table_semantic --target studyplans --force

docker compose --env-file .env.docker --profile jobs run --rm import_data sh -lc "npx ts-node src/import/prune_staging_to_current_program_documents.ts"

docker compose --env-file .env.docker --profile jobs run --rm import_data sh -lc "npx ts-node src/import/import_consist_of.ts"

python scrapy_crawler\outputs\parsed_fulltext_docling_new_clean\cleanup_historical_unifr_docs.py ^  --index scrapy_crawler\outputs\parsed_fulltext_docling_new_clean\_index.jsonl ^  --parsed-dir scrapy_crawler\outputs\parsed_fulltext_docling_new_clean ^  --apply

set RAG_PARSER=docling_language_aware
python -m chatbot.app.build_faiss_docling_language_aware --target studyplans --force

validation 
1. programs
cd scrapy_crawler
scrapy crawl expected_programs -O scrapy_crawler/validation/metrics/compare_programs/programs.json

cd scrapy_crawler\validation
- python compare_programs.py
- python validate_courses.py ^  --courses ../spider_outputs/courses.json ^  --output-prefix courses
- python validate_programs.py ^  --programs-file ../spider_outputs/programmes_with_curricula_enriched.json ^  --output-dir ./metrics/validate_programs_curricula
- python validate_programs.py ^  --programs-file ../spider_outputs/program_links_with_ects_and_docs.json ^  --output-dir ./metrics/validate_programs_docs
- python validation\validate_doc_downloads.py ^  --spider-outputs spider_outputs ^  --manifest ..\outputs\_program_docs_manifest.json ^  --out validation\metrics\documents_downloads\document_download_quality.json
- python validation\integrity_score.py ^  --metrics-dir validation\metrics ^  --out validation\metrics\scores\json_integrity_score.json
- python validation\validate_doc_parsing.py ^  --outputs-root ..\outputs ^  --manifest ..\outputs\_program_docs_manifest.json ^  --out validation\metrics\documents_parsing\document_parsing_quality.json

- npx ts-node DB_service\src\import\validate_database_quality.ts ^
  --out scrapy_crawler\scrapy_crawler\validation\metrics\database\database_quality.json

Timeout for too long questions
- python eval_runner_chatbot.py ^
  --input thesis_chatbot_evaluation_template.xlsx ^
  --output test_results.xlsx ^
  --timeout 2000 ^
  --limit 2 ^
  --systems ^
    rag=http://localhost:8000/ask:rag ^
    auto=http://localhost:8000/ask:auto ^
    hybrid=http://localhost:8000/ask:hybrid ^
    tool=http://localhost:8000/ask:tool

    python eval_runner_chatbot.py ^
  --input thesis_chatbot_evaluation_template.xlsx ^
  --output test_results.xlsx ^
  --timeout 2000 ^
  --limit 2 ^
  --systems ^
    auto=http://localhost:8000/ask:auto ^
    tool=http://localhost:8000/ask:tool ^
    rag=http://localhost:8000/ask:rag

    python eval_runner_chatbot.py ^
  --input thesis_chatbot_evaluation_template.xlsx ^
  --output test_results.xlsx ^
  --timeout 2000 ^
  --systems ^
    auto=http://localhost:8000/ask:auto ^
    tool=http://localhost:8000/ask:tool ^
    rag=http://localhost:8000/ask:rag
    

# Run locally

## Requirements

- Docker Desktop
- Git

## Setup

```bash
git clone <repo>
cd <repo>

cp .env.example .env

docker compose up --build
```

Backend:
http://localhost:3000

Chatbot:
http://localhost:8000


cd scrapy_crawler
cd scrapy_crawler
scrapy crawl structure_of_studies -O spider_outputs/base_info/structure_of_studies.json
scrapy crawl student_advice_and_information -O spider_outputs/base_info/student_advice_and_information.json
scrapy crawl languages_of_study -O spider_outputs/base_info/languages_of_study.json
scrapy crawl unifr_examinations_faculty_rules -O spider_outputs/base_info/unifr_examinations_faculty_rules.json
scrapy crawl unifr_elite_sports -O spider_outputs/base_info/unifr_elite_sports.json
scrapy crawl unifr_studies_disability -O spider_outputs/base_info/unifr_studies_disability.json
scrapy crawl unifr_studies_army -O spider_outputs/base_info/unifr_studies_army.json

scrapy crawl unifr_infrastructures -O spider_outputs/base_info/unifr_infrastructures.json
scrapy crawl unifr_activities -O spider_outputs/base_info/unifr_activities.json
scrapy crawl unifr_living_in_fribourg -O spider_outputs/base_info/unifr_living_in_fribourg.json
scrapy crawl unifr_life_in_fribourg -O spider_outputs/base_info/unifr_life_in_fribourg.json


python chatbot/app/create_base_faiss_vectorstore.py


python run_rag_retrieval_eval.py --input thesis_chatbot_evaluation_template.xlsx --output rag_retrieval_eval_results.xlsx --base-url http://localhost:8000 --limit 15


python run_rag_retrieval_only_eval.py --input thesis_chatbot_evaluation_template_rag.xlsx --output rag_retrieval_only_possible_results.xlsx --base-url http://localhost:8000 

python run_rag_retrieval_only_eval.py --input thesis_chatbot_evaluation_template_rag.xlsx --output rag_retrieval_only_possible_results_new6.xlsx --base-url http://localhost:8000 

python run_ask_answer_eval.py --input thesis_chatbot_evaluation_template_rag.xlsx --output rag_ask_only_possible_results.xlsx --base-url http://localhost:8000 

python DB_service\src\import\normalize_faculty_documents.py ^  --input-dir scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs ^  --out scrapy_crawler\scrapy_crawler\spider_outputs\faculty_documents_normalized.json

python DB_service\src\import\match_faculty_docs_to_programs.py ^ 
  --programmes scrapy_crawler\scrapy_crawler\spider_outputs\programmes_with_curricula_enriched.json ^
  --docs scrapy_crawler\scrapy_crawler\spider_outputs\faculty_documents_normalized.json ^
  --out scrapy_crawler\scrapy_crawler\spider_outputs\programmes_with_faculty_documents.json ^
  --audit-out scrapy_crawler\scrapy_crawler\spider_outputs\document_program_match_audit.json ^
  --unmatched-docs-out scrapy_crawler\scrapy_crawler\spider_outputs\unmatched_faculty_documents.json

npx tsx DB_service/src/import/01_download_faculty_docs_v3.ts ^  --matched-input ./scrapy_crawler/scrapy_crawler/spider_outputs/programmes_with_faculty_documents_patched.json ^  --unmatched-input ./scrapy_crawler/scrapy_crawler/spider_outputs/unmatched_faculty_documents_remaining.json ^  --out ./scrapy_crawler/outputs/faculty_docs_v3 ^  --concurrency 6