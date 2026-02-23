BEGIN;

-- (optional) ensure unique index exists once
CREATE UNIQUE INDEX IF NOT EXISTS uq_programcourse_program_code
ON programCourse (program_id, code);

-- Load “consistent” table from staging (1 row per program+code)
INSERT INTO programCourse (program_id, code, course_type)
SELECT DISTINCT ON (s.program_id, s.extracted_code)
  s.program_id,
  s.extracted_code AS code,
  CASE
    WHEN s.raw_text ~* '(wahl(pflicht)?|wahlmodule|optional|elective|à\s*choix|a\s*choix|option(s)?|facultatif)'
      THEN 'Elective'
    WHEN s.raw_text ~* '(obligator(isch|y)?|pflicht|mandatory|required|obligatoire)'
      THEN 'Mandatory'
    ELSE 'Mandatory'
  END AS course_type
FROM programCourseStaging s
WHERE s.extracted_code IS NOT NULL
ORDER BY
  s.program_id,
  s.extracted_code,
  (CASE
     WHEN s.raw_text ~* '(wahl(pflicht)?|optional|elective|à\s*choix|option|facultatif|obligator|pflicht|mandatory|required|obligatoire)'
       THEN 0
     ELSE 1
   END) ASC,
  length(s.raw_text) DESC,
  s.page_no ASC,
  s.staging_id ASC
ON CONFLICT (program_id, code)
DO UPDATE SET
  course_type = EXCLUDED.course_type;

COMMIT;