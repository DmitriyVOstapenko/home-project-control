import { createInterface } from "node:readline";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const SERVER_VERSION = "0.5.0";
const SETUP_TEMPLATE_URI = "ui://home-project-control/project-startup.html";
const TEMPLATE_URI = "ui://home-project-control/project-intake.html";
const CHOICE_TEMPLATE_URI = "ui://home-project-control/choice-form.html";
const currentDir = dirname(fileURLToPath(import.meta.url));
const setupWidgetHtml = readFileSync(join(currentDir, "project-startup.html"), "utf8");
const widgetHtml = readFileSync(join(currentDir, "project-intake.html"), "utf8");
const choiceWidgetHtml = readFileSync(join(currentDir, "choice-form.html"), "utf8");
const structureSpec = JSON.parse(readFileSync(join(currentDir, "project-structure.json"), "utf8"));

const catalogs = {
  mode: {
    new_project: "Новый проект",
    continue_project: "Продолжить существующий проект",
    pre_purchase: "Проверить объект перед покупкой"
  },
  objectType: {
    detached_house: "Частный дом",
    apartment: "Квартира",
    townhouse: "Таунхаус",
    unfinished_house: "Недостроенный дом",
    land: "Участок под будущий дом",
    other: "Другой объект"
  },
  projectStage: {
    purchase_review: "Проверка перед покупкой",
    concept: "Формирование задачи",
    design: "Проектирование",
    permits: "Согласования и подключения",
    construction: "Строительство",
    systems: "Инженерные системы",
    finishing: "Отделка",
    repair: "Ремонт или замена",
    handover: "Приёмка",
    operation: "Эксплуатация"
  },
  nearestGoal: {
    start_project: "Создать и настроить проект",
    assess_property: "Проверить объект перед покупкой",
    scope_systems: "Определить состав систем и работ",
    check_proposal: "Проверить предложение подрядчика",
    compare_purchase: "Проверить цену закупки и альтернативы",
    inspect_work: "Проверить выполненные работы",
    track_progress: "Собрать ход работ и расходы",
    other: "Другая ближайшая задача"
  },
  systems: {
    electrical: "Электроснабжение",
    gas: "Газоснабжение",
    heating: "Отопление",
    water: "Водоснабжение",
    sewer: "Канализация или септик",
    ventilation: "Вентиляция",
    conditioning: "Кондиционирование и влажность",
    network: "Интернет и слаботочные сети",
    security: "Видеонаблюдение, охрана и доступ",
    safety: "Пожарная защита и защита от протечек",
    automation: "Автоматизация и умный дом",
    unsure: "Пока не знаю"
  },
  works: {
    survey_design: "Обследование и проектирование",
    structures: "Конструкции, кровля и фасад",
    demolition: "Демонтаж",
    soundproofing: "Звукоизоляция",
    partitions: "Перегородки",
    floors: "Стяжка и полы",
    walls: "Выравнивание и покраска стен",
    ceilings: "Потолки",
    tile: "Плитка",
    plumbing: "Сантехника",
    doors: "Окна и двери",
    furniture: "Кухня и встроенная мебель",
    landscaping: "Участок и благоустройство",
    unsure: "Пока не знаю"
  },
  documents: {
    title_docs: "Документы на объект",
    surveys: "Обследования и изыскания",
    plans: "Планы, проекты и чертежи",
    specifications: "Спецификации и ведомости",
    proposals: "Коммерческие предложения и сметы",
    contracts: "Договоры и приложения",
    invoices: "Счета, чеки и подтверждения оплат",
    photos: "Фото или видео объекта и работ",
    none: "Документов пока нет",
    unsure: "Не знаю, что есть"
  }
};

const arrayFields = ["systems", "works", "documents"];
const scalarFields = ["mode", "objectType", "projectStage", "nearestGoal"];

const setupActions = {
  select_existing: "Выбрать существующий проект",
  create_new: "Создать новый проект",
  repair_current: "Восстановить недостающую структуру текущего проекта"
};

function toolDefinitions() {
  return [
    {
      name: "inspect_project_workspace",
      title: "Автоматически проверить проект и структуру",
      description: "Обязательный первый инструмент при каждом новом обращении к плагину. Самостоятельно проверяет текущую рабочую папку: создан ли проект плагином «Контроль дома», совпадает ли привязка и присутствует ли вся обязательная структура папок и служебных файлов. Не задаёт пользователю вопросов и ничего не изменяет.",
      inputSchema: {
        type: "object",
        properties: {
          workspacePath: { type: "string", maxLength: 1024, description: "Точный абсолютный путь текущей рабочей папки задачи. Не придумывать и не подставлять путь исходников плагина." }
        },
        required: ["workspacePath"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          verified: { type: "boolean" },
          gatePassed: { type: "boolean" },
          status: { type: "string" },
          workspacePath: { type: "string" },
          projectName: { type: ["string", "null"] },
          projectId: { type: ["string", "null"] },
          schemaVersion: { type: ["string", "null"] },
          boundPath: { type: ["string", "null"] },
          createdByPlugin: { type: "boolean" },
          legacyProject: { type: "boolean" },
          structureState: { type: "string" },
          missingDirectories: { type: "array", items: { type: "string" } },
          missingFiles: { type: "array", items: { type: "string" } },
          invalidFiles: { type: "array", items: { type: "string" } },
          nextInstruction: { type: "string" }
        },
        required: ["verified", "gatePassed", "status", "workspacePath", "projectName", "projectId", "schemaVersion", "boundPath", "createdByPlugin", "legacyProject", "structureState", "missingDirectories", "missingFiles", "invalidFiles", "nextInstruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    },
    {
      name: "open_project_setup_form",
      title: "Выбрать или создать проект",
      description: "Открывает визуальный мастер только если автоматическая проверка не нашла полностью исправный текущий проект. Даёт выбрать существующую папку проекта, создать новый проект или восстановить недостающую структуру текущего проекта.",
      inputSchema: {
        type: "object",
        properties: {
          workspacePath: { type: "string", maxLength: 1024 },
          prefill: { type: "object", additionalProperties: true }
        },
        required: ["workspacePath"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          formVersion: { type: "string" },
          workspacePath: { type: "string" },
          diagnosis: { type: "object" },
          prefill: { type: "object" },
          instruction: { type: "string" }
        },
        required: ["formVersion", "workspacePath", "diagnosis", "prefill", "instruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      _meta: {
        ui: { resourceUri: SETUP_TEMPLATE_URI },
        "openai/outputTemplate": SETUP_TEMPLATE_URI,
        "openai/toolInvocation/invoking": "Готовлю выбор проекта…",
        "openai/toolInvocation/invoked": "Мастер проекта готов"
      }
    },
    {
      name: "submit_project_setup",
      title: "Проверить выбор проекта",
      description: "Принимает выбор из визуального мастера, проверяет выбранную папку и возвращает точный безопасный следующий шаг. Сам не создаёт и не изменяет файлы.",
      inputSchema: {
        type: "object",
        properties: {
          action: { type: "string", enum: Object.keys(setupActions) },
          workspacePath: { type: "string", minLength: 1, maxLength: 1024 },
          targetPath: { type: "string", maxLength: 1024 },
          projectName: { type: "string", maxLength: 200 }
        },
        required: ["action", "workspacePath"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          accepted: { type: "boolean" },
          gatePassed: { type: "boolean" },
          action: { type: "string" },
          actionLabel: { type: "string" },
          targetPath: { type: "string" },
          projectName: { type: ["string", "null"] },
          diagnosis: { type: "object" },
          requiresCreation: { type: "boolean" },
          requiresRepair: { type: "boolean" },
          repairMode: { type: ["string", "null"] },
          requiresWorkspaceSwitch: { type: "boolean" },
          nextInstruction: { type: "string" }
        },
        required: ["accepted", "gatePassed", "action", "actionLabel", "targetPath", "projectName", "diagnosis", "requiresCreation", "requiresRepair", "repairMode", "requiresWorkspaceSwitch", "nextInstruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    },
    {
      name: "verify_project_context",
      title: "Повторно проверить проект после создания или восстановления",
      description: "Повторяет полную проверку карточки, привязки, папок и служебных файлов. Вызывать после init_project.py, восстановления структуры или смены рабочей папки.",
      inputSchema: {
        type: "object",
        properties: { workspacePath: { type: "string", minLength: 1, maxLength: 1024 } },
        required: ["workspacePath"],
        additionalProperties: false
      },
      outputSchema: { type: "object", additionalProperties: true },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    },
    {
      name: "open_project_intake_form",
      title: "Открыть анкету проекта дома",
      description: "Показывает основную анкету проекта после обязательной автоматической проверки. Вызывать только если inspect_project_workspace или verify_project_context вернул gatePassed/verified: true.",
      inputSchema: {
        type: "object",
        properties: {
          prefill: {
            type: "object",
            description: "Уже подтверждённые ответы, если они известны. Не заполняй догадками.",
            additionalProperties: true
          }
        },
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          formVersion: { type: "string" },
          prefill: { type: "object" },
          instruction: { type: "string" }
        },
        required: ["formVersion", "prefill", "instruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      _meta: {
        ui: { resourceUri: TEMPLATE_URI },
        "openai/outputTemplate": TEMPLATE_URI,
        "openai/toolInvocation/invoking": "Открываю анкету…",
        "openai/toolInvocation/invoked": "Анкета готова"
      }
    },
    {
      name: "submit_project_intake",
      title: "Проверить ответы анкеты проекта",
      description: "Проверяет и нормализует ответы из интерактивной анкеты. Инструмент не создаёт папки, не пишет файлы и не принимает строительные решения.",
      inputSchema: {
        type: "object",
        properties: {
          mode: { type: "string", enum: Object.keys(catalogs.mode) },
          objectType: { type: "string", enum: Object.keys(catalogs.objectType) },
          projectStage: { type: "string", enum: Object.keys(catalogs.projectStage) },
          nearestGoal: { type: "string", enum: Object.keys(catalogs.nearestGoal) },
          systems: { type: "array", items: { type: "string", enum: Object.keys(catalogs.systems) }, uniqueItems: true },
          works: { type: "array", items: { type: "string", enum: Object.keys(catalogs.works) }, uniqueItems: true },
          documents: { type: "array", items: { type: "string", enum: Object.keys(catalogs.documents) }, uniqueItems: true },
          objectName: { type: "string", maxLength: 200 },
          location: { type: "string", maxLength: 300 },
          notes: { type: "string", maxLength: 2000 }
        },
        required: ["mode", "objectType", "projectStage", "nearestGoal", "systems", "works", "documents"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          accepted: { type: "boolean" },
          selection: { type: "object" },
          labels: { type: "object" },
          nextInstruction: { type: "string" }
        },
        required: ["accepted", "selection", "labels", "nextInstruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    },
    {
      name: "open_choice_form",
      title: "Открыть форму выбора",
      description: "Показывает универсальную интерактивную форму для одного или нескольких закрытых вопросов. Используй её во всех скиллах плагина, когда пользователь может выбрать готовый вариант или несколько вариантов вместо печати текста. Передавай только релевантные варианты и не подставляй факты за пользователя.",
      inputSchema: {
        type: "object",
        properties: {
          title: { type: "string", minLength: 1, maxLength: 120 },
          description: { type: "string", maxLength: 500 },
          questions: {
            type: "array",
            minItems: 1,
            maxItems: 6,
            items: {
              type: "object",
              properties: {
                id: { type: "string", pattern: "^[a-z][a-z0-9_]{0,39}$" },
                prompt: { type: "string", minLength: 1, maxLength: 240 },
                helpText: { type: "string", maxLength: 300 },
                type: { type: "string", enum: ["single", "multiple"] },
                required: { type: "boolean" },
                options: {
                  type: "array",
                  minItems: 2,
                  maxItems: 20,
                  items: {
                    type: "object",
                    properties: {
                      value: { type: "string", pattern: "^[a-z0-9][a-z0-9_-]{0,49}$" },
                      label: { type: "string", minLength: 1, maxLength: 120 },
                      description: { type: "string", maxLength: 240 }
                    },
                    required: ["value", "label"],
                    additionalProperties: false
                  }
                }
              },
              required: ["id", "prompt", "type", "options"],
              additionalProperties: false
            }
          },
          prefill: { type: "object", additionalProperties: true }
        },
        required: ["title", "questions"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          formVersion: { type: "string" },
          formId: { type: "string" },
          title: { type: "string" },
          description: { type: "string" },
          questions: { type: "array" },
          prefill: { type: "object" },
          instruction: { type: "string" }
        },
        required: ["formVersion", "formId", "title", "description", "questions", "prefill", "instruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      _meta: {
        ui: { resourceUri: CHOICE_TEMPLATE_URI },
        "openai/outputTemplate": CHOICE_TEMPLATE_URI,
        "openai/toolInvocation/invoking": "Готовлю варианты…",
        "openai/toolInvocation/invoked": "Форма выбора готова"
      }
    },
    {
      name: "submit_choice_form",
      title: "Проверить ответы формы выбора",
      description: "Проверяет ответы универсальной интерактивной формы и возвращает выбранные значения с подписями. Ничего не записывает и не выполняет выбранное действие.",
      inputSchema: {
        type: "object",
        properties: {
          formId: { type: "string", minLength: 1, maxLength: 80 },
          title: { type: "string", minLength: 1, maxLength: 120 },
          questions: { type: "array", minItems: 1, maxItems: 6, items: { type: "object" } },
          answers: { type: "object", additionalProperties: true }
        },
        required: ["formId", "title", "questions", "answers"],
        additionalProperties: false
      },
      outputSchema: {
        type: "object",
        properties: {
          accepted: { type: "boolean" },
          formId: { type: "string" },
          answers: { type: "object" },
          labels: { type: "object" },
          nextInstruction: { type: "string" }
        },
        required: ["accepted", "formId", "answers", "labels", "nextInstruction"]
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    }
  ];
}

function comparablePath(value) {
  const normalized = resolve(value).replace(/[\\/]+$/, "");
  return process.platform === "win32" ? normalized.toLocaleLowerCase("en-US") : normalized;
}

function inspectManagedStructure(root) {
  const expectedDirectories = [...structureSpec.folders, ...structureSpec.control_directories];
  const expectedFiles = [
    ".home-control/project.json",
    ...Object.keys(structureSpec.json_files),
    ...structureSpec.jsonl_files,
    ...Object.keys(structureSpec.csv_files)
  ];
  const missingDirectories = [];
  const missingFiles = [];
  const invalidFiles = [];
  let recognizedCount = 0;

  for (const relative of expectedDirectories) {
    const target = join(root, relative);
    if (!existsSync(target)) missingDirectories.push(relative);
    else if (!statSync(target).isDirectory()) invalidFiles.push(`${relative}: ожидалась папка`);
    else recognizedCount += 1;
  }
  for (const relative of expectedFiles) {
    const target = join(root, relative);
    if (!existsSync(target)) missingFiles.push(relative);
    else if (!statSync(target).isFile()) invalidFiles.push(`${relative}: ожидался файл`);
    else recognizedCount += 1;
  }

  for (const relative of Object.keys(structureSpec.json_files)) {
    const target = join(root, relative);
    if (!existsSync(target) || !statSync(target).isFile()) continue;
    try {
      const parsed = JSON.parse(readFileSync(target, "utf8"));
      if (relative.endsWith("documents.json") && !Array.isArray(parsed?.items)) invalidFiles.push(`${relative}: отсутствует массив items`);
    } catch {
      invalidFiles.push(`${relative}: некорректный JSON`);
    }
  }
  for (const relative of structureSpec.jsonl_files) {
    const target = join(root, relative);
    if (!existsSync(target) || !statSync(target).isFile()) continue;
    const lines = readFileSync(target, "utf8").split(/\r?\n/);
    for (let index = 0; index < lines.length; index += 1) {
      if (!lines[index].trim()) continue;
      try { JSON.parse(lines[index]); }
      catch { invalidFiles.push(`${relative}:${index + 1}: некорректный JSONL`); break; }
    }
  }
  for (const [relative, requiredHeaders] of Object.entries(structureSpec.csv_files)) {
    const target = join(root, relative);
    if (!existsSync(target) || !statSync(target).isFile()) continue;
    const firstLine = readFileSync(target, "utf8").replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0];
    const actualHeaders = firstLine.split(",").map(value => value.trim());
    const missingHeaders = requiredHeaders.filter(value => !actualHeaders.includes(value));
    if (missingHeaders.length) invalidFiles.push(`${relative}: отсутствуют колонки ${missingHeaders.join(", ")}`);
  }

  const structureState = invalidFiles.length
    ? "invalid"
    : missingDirectories.length || missingFiles.length
      ? recognizedCount ? "partial" : "absent"
      : "complete";
  return { structureState, missingDirectories, missingFiles, invalidFiles, recognizedCount };
}

function inspectProjectContext(workspacePath) {
  const requestedPath = cleanText(workspacePath, 1024);
  const empty = {
    verified: false,
    gatePassed: false,
    status: "workspace_unknown",
    workspacePath: requestedPath,
    markerPath: requestedPath ? join(resolve(requestedPath), ".home-control", "project.json") : "",
    projectName: null,
    projectId: null,
    schemaVersion: null,
    boundPath: null,
    createdByPlugin: false,
    legacyProject: false,
    structureState: "unknown",
    missingDirectories: [],
    missingFiles: [],
    invalidFiles: [],
    nextInstruction: "Не удалось определить текущую рабочую папку. Открой задачу с папкой проекта или укажи точный путь и повтори автоматическую проверку."
  };
  if (!requestedPath) return empty;
  let actualPath;
  try {
    actualPath = realpathSync(resolve(requestedPath));
    if (!statSync(actualPath).isDirectory()) return { ...empty, status: "not_directory", workspacePath: actualPath, nextInstruction: "Указанный путь не является папкой. Открой папку проекта как рабочую папку задачи и повтори проверку." };
  } catch {
    return { ...empty, status: "folder_unavailable", nextInstruction: "Текущая папка не существует или недоступна. Открой существующую папку проекта и повтори проверку." };
  }
  const markerPath = join(actualPath, ".home-control", "project.json");
  const structure = inspectManagedStructure(actualPath);
  const base = { ...empty, ...structure, workspacePath: actualPath, markerPath };
  if (!existsSync(markerPath)) {
    const firstRun = structure.recognizedCount === 0;
    return {
      ...base,
      status: firstRun ? "first_run" : "structure_without_project_marker",
      nextInstruction: firstRun
        ? "Проект плагина в текущей папке не найден. Открой визуальный мастер выбора или создания проекта."
        : "В папке есть часть узнаваемой структуры, но нет карточки проекта. Открой визуальный мастер: пользователь должен выбрать другую папку либо явно создать проект в этой папке; не считай её проектной автоматически."
    };
  }
  let project;
  try {
    project = JSON.parse(readFileSync(markerPath, "utf8"));
  } catch {
    return { ...base, status: "project_marker_invalid", nextInstruction: "Карточка проекта существует, но не читается как корректный JSON. Не продолжай работу с документами, пока файл не будет проверен или восстановлен." };
  }
  const projectName = cleanText(project?.name, 200) || null;
  const projectId = cleanText(project?.project_id, 200) || null;
  const schemaVersion = cleanText(project?.schema_version, 40) || null;
  const boundPath = cleanText(project?.folder_binding?.absolute_path || project?.project_root, 1024) || null;
  const pluginId = cleanText(project?.created_by?.plugin_id, 100) || null;
  const createdByPlugin = pluginId === structureSpec.plugin_id;
  const legacyProject = !pluginId && /^2\./.test(schemaVersion || "");
  const identified = { ...base, projectName, projectId, schemaVersion, boundPath, createdByPlugin, legacyProject };
  if (pluginId && !createdByPlugin) {
    return { ...identified, status: "foreign_project_marker", nextInstruction: "Карточка в текущей папке принадлежит другому инструменту. Не изменяй её и предложи выбрать другую папку или создать новый проект отдельно." };
  }
  if (!projectName || !schemaVersion) {
    return { ...identified, status: "project_marker_incomplete", nextInstruction: "Карточка проекта найдена, но в ней отсутствует название или версия схемы. Не продолжай до проверки карточки проекта." };
  }
  if (!boundPath) {
    return { ...identified, status: "folder_binding_missing", nextInstruction: "Карточка проекта найдена, но в ней не сохранён путь привязанной папки. Не продолжай автоматически: сначала подтверди, что это нужный проект, покажи текущий полный путь и получи отдельное согласие на обновление привязки." };
  }
  if (boundPath && comparablePath(boundPath) !== comparablePath(actualPath)) {
    return { ...identified, status: "folder_binding_mismatch", nextInstruction: "Карточка проекта привязана к другой папке. Не продолжай автоматически: покажи сохранённый и текущий пути и попроси владельца выбрать правильную папку либо отдельно разрешить перепривязку." };
  }
  if (structure.invalidFiles.length) {
    return { ...identified, status: "project_structure_invalid", nextInstruction: "Проект распознан, но некоторые существующие служебные файлы или пути имеют неверный формат. Не перезаписывай их автоматически; покажи список и предложи безопасное восстановление с сохранением копий." };
  }
  if (structure.missingDirectories.length || structure.missingFiles.length) {
    return { ...identified, status: "project_structure_incomplete", nextInstruction: "Проект распознан, но часть обязательной структуры отсутствует. Открой визуальный мастер и предложи создать только недостающие папки и служебные файлы без изменения существующих документов." };
  }
  return {
    ...identified,
    verified: true,
    gatePassed: true,
    status: "existing_project_ready",
    nextInstruction: "Ранее созданный проект и вся обязательная структура подтверждены. Продолжай работу в текущем проекте без стартовых вопросов."
  };
}

function openSetupResult(input = {}) {
  const workspacePath = cleanText(input.workspacePath, 1024);
  const prefill = input.prefill && typeof input.prefill === "object" && !Array.isArray(input.prefill) ? input.prefill : {};
  const diagnosis = inspectProjectContext(workspacePath);
  return {
    structuredContent: {
      formVersion: SERVER_VERSION,
      workspacePath: diagnosis.workspacePath || workspacePath,
      diagnosis,
      prefill,
      instruction: diagnosis.verified
        ? "Текущий проект исправен; дополнительный выбор не требуется."
        : "Выберите существующий проект, создайте новый или восстановите недостающую структуру текущего проекта, если такой вариант доступен."
    },
    content: [{ type: "text", text: diagnosis.verified
      ? "Текущий проект уже готов к работе."
      : "Открыт визуальный мастер выбора, создания или восстановления проекта." }]
  };
}

function submitSetupResult(input = {}) {
  const action = cleanText(input.action, 40);
  const workspacePath = cleanText(input.workspacePath, 1024);
  const projectName = cleanText(input.projectName, 200) || null;
  if (!Object.hasOwn(setupActions, action)) return { isError: true, content: [{ type: "text", text: "Не выбран допустимый вариант работы с проектом." }] };
  if (!workspacePath) return { isError: true, content: [{ type: "text", text: "Не передан путь текущей рабочей папки." }] };

  const rawTarget = action === "repair_current" ? workspacePath : cleanText(input.targetPath, 1024);
  if (!rawTarget) return { isError: true, content: [{ type: "text", text: "Укажите полный путь к папке проекта." }] };
  if (action === "create_new" && !projectName) return { isError: true, content: [{ type: "text", text: "Укажите название нового проекта." }] };

  const diagnosis = inspectProjectContext(rawTarget);
  const targetPath = diagnosis.workspacePath || resolve(rawTarget);
  const requiresWorkspaceSwitch = comparablePath(targetPath) !== comparablePath(workspacePath);
  let gatePassed = false;
  let requiresCreation = false;
  let requiresRepair = false;
  let repairMode = null;
  let nextInstruction;

  if (action === "select_existing") {
    if (diagnosis.verified) {
      gatePassed = !requiresWorkspaceSwitch;
      nextInstruction = requiresWorkspaceSwitch
        ? "Выбранный проект исправен, но текущая задача открыта в другой рабочей папке. Открой выбранную папку как проектную рабочую папку и запусти плагин там: он подтвердит проект автоматически."
        : "Существующий проект и структура подтверждены. Продолжай работу без создания файлов.";
    } else if (diagnosis.status === "project_structure_incomplete") {
      requiresRepair = true;
      repairMode = "missing_only";
      nextInstruction = "Пользователь выбрал существующий проект с неполной структурой. Покажи перечень недостающих элементов, запусти init_project.py сначала с --dry-run, затем создай только недостающее и повтори verify_project_context.";
    } else if (diagnosis.status === "project_structure_invalid") {
      requiresRepair = true;
      repairMode = "backup_and_restore";
      nextInstruction = "Пользователь выбрал существующий проект с повреждёнными служебными файлами. Открой мастер повторно для явного выбора восстановления либо предложи другую папку; до восстановления проектные документы не анализируй.";
    } else if (diagnosis.status === "first_run" || diagnosis.status === "structure_without_project_marker") {
      nextInstruction = "В выбранной папке проект плагина не найден. Вернись к визуальному мастеру и предложи создать новый проект, запросив его название и полный путь.";
    } else {
      nextInstruction = diagnosis.nextInstruction;
    }
  } else if (action === "create_new") {
    if (!["first_run", "folder_unavailable", "structure_without_project_marker"].includes(diagnosis.status)) {
      return { isError: true, content: [{ type: "text", text: "В выбранной папке уже обнаружена карточка проекта или конфликтующая структура. Не создавай новый проект поверх неё; выбери другую папку или восстановление." }] };
    }
    requiresCreation = true;
    nextInstruction = "Пользователь выбрал создание нового проекта и подтвердил название с полным путём. Выполни init_project.py сначала с --dry-run, покажи создаваемые элементы, затем создай отсутствующую структуру и повтори verify_project_context. Не изменяй существующие пользовательские файлы.";
  } else {
    if (!["project_structure_incomplete", "project_structure_invalid"].includes(diagnosis.status)) {
      return { isError: true, content: [{ type: "text", text: "Текущий проект нельзя восстановить этим безопасным способом: карточка и привязка должны быть исправны, а проблема должна относиться только к управляемой структуре или служебным реестрам." }] };
    }
    requiresRepair = true;
    repairMode = diagnosis.status === "project_structure_invalid" ? "backup_and_restore" : "missing_only";
    nextInstruction = repairMode === "backup_and_restore"
      ? "Пользователь явно выбрал безопасное восстановление повреждённых служебных файлов. Выполни repair_project.py без --apply, покажи план и расположение будущих резервных копий, затем выполни его с --apply и повтори verify_project_context. Оригиналы проектных документов не изменяй."
      : "Пользователь явно выбрал восстановление текущего проекта. Выполни init_project.py сначала с --dry-run, затем создай только недостающие элементы и повтори verify_project_context. Существующие файлы не перезаписывай.";
  }

  return {
    structuredContent: {
      accepted: true,
      gatePassed,
      action,
      actionLabel: setupActions[action],
      targetPath,
      projectName: projectName || diagnosis.projectName,
      diagnosis,
      requiresCreation,
      requiresRepair,
      repairMode,
      requiresWorkspaceSwitch,
      nextInstruction
    },
    content: [{ type: "text", text: nextInstruction }]
  };
}

function cleanChoiceForm(input = {}) {
  const errors = [];
  const title = cleanText(input.title, 120);
  const description = cleanText(input.description, 500);
  if (!title) errors.push("Не задан заголовок формы");
  if (!Array.isArray(input.questions) || input.questions.length < 1 || input.questions.length > 6) {
    errors.push("Форма должна содержать от 1 до 6 вопросов");
  }
  const ids = new Set();
  const questions = [];
  for (const raw of Array.isArray(input.questions) ? input.questions.slice(0, 6) : []) {
    const id = cleanText(raw?.id, 40);
    const prompt = cleanText(raw?.prompt, 240);
    const type = raw?.type === "multiple" ? "multiple" : raw?.type === "single" ? "single" : "";
    if (!/^[a-z][a-z0-9_]{0,39}$/.test(id) || ids.has(id)) { errors.push(`Некорректный или повторяющийся id вопроса: ${id || "пусто"}`); continue; }
    ids.add(id);
    if (!prompt) errors.push(`У вопроса ${id} нет текста`);
    if (!type) errors.push(`У вопроса ${id} не выбран тип`);
    const optionValues = new Set();
    const options = [];
    for (const option of Array.isArray(raw?.options) ? raw.options.slice(0, 20) : []) {
      const value = cleanText(option?.value, 50);
      const label = cleanText(option?.label, 120);
      if (!/^[a-z0-9][a-z0-9_-]{0,49}$/.test(value) || optionValues.has(value) || !label) {
        errors.push(`Некорректный или повторяющийся вариант в вопросе ${id}`);
        continue;
      }
      optionValues.add(value);
      options.push({ value, label, description: cleanText(option?.description, 240) });
    }
    if (options.length < 2) errors.push(`У вопроса ${id} должно быть не менее двух вариантов`);
    questions.push({ id, prompt, helpText: cleanText(raw?.helpText, 300), type, required: raw?.required !== false, options });
  }
  const prefill = input.prefill && typeof input.prefill === "object" && !Array.isArray(input.prefill) ? input.prefill : {};
  return { title, description, questions, prefill, errors };
}

function choiceFormId(form) {
  return `choice-${createHash("sha256").update(JSON.stringify([form.title, form.questions])).digest("hex").slice(0, 16)}`;
}

function openChoiceFormResult(input = {}) {
  const form = cleanChoiceForm(input);
  if (form.errors.length) return { isError: true, content: [{ type: "text", text: `Форма не открыта: ${form.errors.join("; ")}.` }] };
  return {
    structuredContent: {
      formVersion: SERVER_VERSION,
      formId: choiceFormId(form),
      title: form.title,
      description: form.description,
      questions: form.questions,
      prefill: form.prefill,
      instruction: "Выберите ответы и нажмите «Передать ответы в чат». Выбор не запускает действия автоматически."
    },
    content: [{ type: "text", text: `Открыта интерактивная форма «${form.title}». Если интерфейс не отобразился, задай те же вопросы текстом.` }]
  };
}

function submitChoiceFormResult(input = {}) {
  const form = cleanChoiceForm(input);
  if (form.errors.length) return { isError: true, content: [{ type: "text", text: `Ответы не приняты: ${form.errors.join("; ")}.` }] };
  if (input.formId !== choiceFormId(form)) return { isError: true, content: [{ type: "text", text: "Ответы не соответствуют открытой форме." }] };
  const answers = {};
  const labels = {};
  const errors = [];
  const rawAnswers = input.answers && typeof input.answers === "object" && !Array.isArray(input.answers) ? input.answers : {};
  for (const question of form.questions) {
    const allowed = new Map(question.options.map((option) => [option.value, option.label]));
    if (question.type === "single") {
      const value = typeof rawAnswers[question.id] === "string" ? rawAnswers[question.id] : "";
      if (value && !allowed.has(value)) errors.push(`Неизвестный ответ на вопрос ${question.id}`);
      if (!value && question.required) errors.push(`Нет ответа на обязательный вопрос ${question.id}`);
      answers[question.id] = value;
      labels[question.id] = value ? allowed.get(value) : "";
    } else {
      const values = Array.isArray(rawAnswers[question.id]) ? [...new Set(rawAnswers[question.id])] : [];
      if (values.some((value) => !allowed.has(value))) errors.push(`Неизвестный ответ на вопрос ${question.id}`);
      if (!values.length && question.required) errors.push(`Нет ответа на обязательный вопрос ${question.id}`);
      answers[question.id] = values.filter((value) => allowed.has(value));
      labels[question.id] = answers[question.id].map((value) => allowed.get(value));
    }
  }
  if (errors.length) return { isError: true, content: [{ type: "text", text: `Ответы не приняты: ${errors.join("; ")}.` }] };
  return {
    structuredContent: {
      accepted: true,
      formId: input.formId,
      answers,
      labels,
      nextInstruction: "Считай выбранные пункты подтверждёнными пользователем. Кратко отрази их в разговоре и продолжай соответствующий скилл. Сам выбор не является разрешением на запись файлов, оплату, заказ или иное внешнее действие."
    },
    content: [{ type: "text", text: `Пользователь подтвердил ответы формы «${form.title}»: ${JSON.stringify(labels)}.` }]
  };
}

function cleanText(value, limit) {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function normalizeSelection(input = {}) {
  const errors = [];
  const selection = {};
  for (const field of scalarFields) {
    const value = input[field];
    if (!Object.hasOwn(catalogs[field], value)) errors.push(`Не выбран обязательный ответ: ${field}`);
    else selection[field] = value;
  }
  for (const field of arrayFields) {
    if (!Array.isArray(input[field])) {
      errors.push(`Поле ${field} должно быть списком`);
      selection[field] = [];
      continue;
    }
    const values = [...new Set(input[field])];
    const unknown = values.filter((value) => !Object.hasOwn(catalogs[field], value));
    if (unknown.length) errors.push(`Неизвестные значения в ${field}: ${unknown.join(", ")}`);
    selection[field] = values.filter((value) => Object.hasOwn(catalogs[field], value));
  }
  selection.objectName = cleanText(input.objectName, 200);
  selection.location = cleanText(input.location, 300);
  selection.notes = cleanText(input.notes, 2000);
  return { selection, errors };
}

function labeled(selection) {
  const labels = {};
  for (const field of scalarFields) labels[field] = catalogs[field][selection[field]];
  for (const field of arrayFields) labels[field] = selection[field].map((value) => catalogs[field][value]);
  labels.objectName = selection.objectName;
  labels.location = selection.location;
  labels.notes = selection.notes;
  return labels;
}

function successResult(input) {
  const { selection, errors } = normalizeSelection(input);
  if (errors.length) {
    return {
      isError: true,
      content: [{ type: "text", text: `Анкета не принята: ${errors.join("; ")}.` }]
    };
  }
  const labels = labeled(selection);
  return {
    structuredContent: {
      accepted: true,
      selection,
      labels,
      nextInstruction: "Считай эти пункты подтверждёнными пользователем. Кратко повтори их, отметь неизвестное и задай только следующий вопрос, для которого нужен свободный текст или отдельное решение. Не создавай папку без отдельного подтверждения полного пути."
    },
    content: [{
      type: "text",
      text: [
        "Ответы анкеты подтверждены пользователем.",
        `Режим: ${labels.mode}.`,
        `Объект: ${labels.objectType}.`,
        `Этап: ${labels.projectStage}.`,
        `Ближайшая задача: ${labels.nearestGoal}.`,
        `Системы: ${labels.systems.join(", ") || "не выбраны"}.`,
        `Работы: ${labels.works.join(", ") || "не выбраны"}.`,
        `Документы: ${labels.documents.join(", ") || "не выбраны"}.`
      ].join("\n")
    }]
  };
}

function openFormResult(input = {}) {
  const prefill = input && typeof input.prefill === "object" && input.prefill ? input.prefill : {};
  return {
    structuredContent: {
      formVersion: SERVER_VERSION,
      prefill,
      instruction: "Заполните варианты и нажмите «Передать ответы в чат». Ничего не записывается в файлы автоматически."
    },
    content: [{ type: "text", text: "Открыта интерактивная анкета проекта. Если интерфейс не отобразился, задай те же вопросы текстом небольшими блоками." }]
  };
}

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function respond(id, result) {
  write({ jsonrpc: "2.0", id, result });
}

function fail(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  write({ jsonrpc: "2.0", id, error });
}

async function handle(message) {
  if (!message || message.jsonrpc !== "2.0" || typeof message.method !== "string") return;
  const { id, method, params = {} } = message;
  const notification = id === undefined;
  try {
    if (method === "initialize") {
      respond(id, {
        protocolVersion: params.protocolVersion || "2025-06-18",
        capabilities: { tools: { listChanged: false }, resources: { subscribe: false, listChanged: false } },
        serverInfo: { name: "home-project-control-forms", version: SERVER_VERSION },
        instructions: "ОБЯЗАТЕЛЬНО: при первом обращении к плагину в каждой новой задаче сначала вызови inspect_project_workspace с точным текущим workspacePath. Пользователю ничего не спрашивай до результата. Если status=existing_project_ready и gatePassed=true, продолжай сразу. Иначе вызови open_project_setup_form. Не читай проектные документы и не запускай профильные процессы, пока inspect_project_workspace или verify_project_context не вернёт gatePassed/verified=true."
      });
      return;
    }
    if (method === "notifications/initialized" || method === "notifications/cancelled") return;
    if (method === "ping") { respond(id, {}); return; }
    if (method === "tools/list") { respond(id, { tools: toolDefinitions() }); return; }
    if (method === "resources/list") {
      respond(id, { resources: [
        { name: "Выбор или создание проекта", title: "Мастер проекта", uri: SETUP_TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", description: "Визуальный выбор существующего проекта, создание нового или восстановление неполной структуры." },
        { name: "Анкета проекта дома", title: "Анкета проекта дома", uri: TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", description: "Интерактивный выбор типа проекта, систем, работ и документов." },
        { name: "Форма выбора", title: "Универсальная форма выбора", uri: CHOICE_TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", description: "Один или несколько закрытых вопросов с переключателями и галочками." }
      ] });
      return;
    }
    if (method === "resources/templates/list") { respond(id, { resourceTemplates: [] }); return; }
    if (method === "resources/read") {
      if (params.uri === SETUP_TEMPLATE_URI) {
        respond(id, { contents: [{ uri: SETUP_TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", text: setupWidgetHtml, _meta: { ui: { prefersBorder: true } } }] });
        return;
      }
      if (params.uri === TEMPLATE_URI) {
        respond(id, { contents: [{ uri: TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", text: widgetHtml, _meta: { ui: { prefersBorder: true } } }] });
        return;
      }
      if (params.uri === CHOICE_TEMPLATE_URI) {
        respond(id, { contents: [{ uri: CHOICE_TEMPLATE_URI, mimeType: "text/html;profile=mcp-app", text: choiceWidgetHtml, _meta: { ui: { prefersBorder: true } } }] });
        return;
      }
      fail(id, -32602, "Unknown resource URI");
      return;
    }
    if (method === "tools/call") {
      const name = params.name;
      const args = params.arguments || {};
      if (name === "inspect_project_workspace") {
        const inspection = inspectProjectContext(args.workspacePath);
        respond(id, { structuredContent: inspection, content: [{ type: "text", text: inspection.nextInstruction }] });
        return;
      }
      if (name === "open_project_setup_form") { respond(id, openSetupResult(args)); return; }
      if (name === "submit_project_setup") { respond(id, submitSetupResult(args)); return; }
      if (name === "verify_project_context") {
        const verification = inspectProjectContext(args.workspacePath);
        respond(id, { structuredContent: verification, content: [{ type: "text", text: verification.nextInstruction }] });
        return;
      }
      if (name === "open_project_intake_form") { respond(id, openFormResult(args)); return; }
      if (name === "submit_project_intake") { respond(id, successResult(args)); return; }
      if (name === "open_choice_form") { respond(id, openChoiceFormResult(args)); return; }
      if (name === "submit_choice_form") { respond(id, submitChoiceFormResult(args)); return; }
      fail(id, -32602, `Unknown tool: ${String(name)}`);
      return;
    }
    if (!notification) fail(id, -32601, `Method not found: ${method}`);
  } catch (error) {
    if (!notification) fail(id, -32603, "Internal error", String(error?.message || error));
    else process.stderr.write(`Home Project Control MCP error: ${String(error?.stack || error)}\n`);
  }
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });
lines.on("line", (line) => {
  if (!line.trim()) return;
  try { void handle(JSON.parse(line)); }
  catch (error) { process.stderr.write(`Home Project Control MCP invalid JSON: ${String(error?.message || error)}\n`); }
});
