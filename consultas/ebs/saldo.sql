WITH proj AS (
    SELECT project_id, segment1 AS nro_projeto, name AS nome_projeto
    FROM APPS.PA_PROJECTS_ALL WHERE segment1 = :p_project_number
)
SELECT DISTINCT p.nro_projeto, p.nome_projeto, NVL(bl.burdened_cost, 0) AS burdened_cost,
    bl.creation_date AS dt_criacao_linha, bv.version_number
FROM proj p
JOIN APPS.PA_TASKS t ON t.project_id = p.project_id
JOIN APPS.PA_RESOURCE_ASSIGNMENTS ra ON ra.task_id = t.task_id
JOIN APPS.PA_BUDGET_LINES bl ON bl.resource_assignment_id = ra.resource_assignment_id
JOIN APPS.PA_BUDGET_VERSIONS bv ON bv.budget_version_id = bl.budget_version_id
WHERE (bv.current_flag = 'Y' OR bv.budget_status_code = 'B')
  AND (NVL(bl.raw_cost,0)>0 OR NVL(bl.burdened_cost,0)>0 OR NVL(bl.project_raw_cost,0)>0 OR NVL(bl.project_burdened_cost,0)>0)
