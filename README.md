# BA_Thesis_Dev
This Repo is the development environment for the web crawling and DB population for my bachelor thesis. 

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

Merge crawled docs
1. python DB_service\src\import\normalize_faculty_jsons.py ^  --input-dir scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs ^  --out scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs_normalized.json
 
2. python DB_service\src\import\merge_studyplans.py ^  --base scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects.json ^  --inputs scrapy_crawler\scrapy_crawler\spider_outputs\faculty_programs_normalized.json ^  --out scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs.json

3. python DB_service\src\import\unmatched_patch.py ^  --in "scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs.json" ^  --out "scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs_enriched.json"

Download and parse
1. npx ts-node DB_service\src\import\01_download_program_docs_v2.ts --input scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs_enriched.json --out scrapy_crawler/outputs
2. npx ts-node DB_service/src/import/parse_docs_full.ts --root scrapy_crawler/outputs

Do the imports (from DB_service)
1. npx ts-node DB_sercive/src/import/run_faculty_import.ts
2. npx ts-node DB_service/src/import/run_courses_import.ts
3. npx ts-node DB_service\src\import\run_program_import.ts --input scrapy_crawler\scrapy_crawler\spider_outputs\program_links_with_ects_and_docs_enriched.json --out scrapy_crawler\scrapy_crawler\spider_outputs\program_docs_etl


