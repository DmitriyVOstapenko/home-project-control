# Установка «Контроля дома»

## Требования

- актуальная версия ChatGPT desktop app с Codex, Codex CLI с командами `codex plugin` либо Claude Code с командами `claude plugin`;
- Git для установки из публичного репозитория;
- Python 3 для локальных проектных утилит.

Плагин не требует отдельного MCP-сервера, Node.js, HTML-форм, файла `.cmd`, облачного диска или API-ключа.

## Установка из GitHub в Codex

Добавьте публичный GitHub-репозиторий как источник marketplace:

```text
codex plugin marketplace add DmitriyVOstapenko/home-project-control --ref main
```

Установите плагин:

```text
codex plugin add home-project-control@home-project-control-marketplace
```

После установки начните новую задачу Codex: уже открытая задача не подхватывает новый набор скиллов автоматически.

## Установка из GitHub в Claude Code

Добавьте публичный репозиторий как marketplace и установите плагин в пользовательской области:

```text
claude plugin marketplace add https://github.com/DmitriyVOstapenko/home-project-control.git
claude plugin install home-project-control@home-project-control-marketplace
```

Если установщик сообщает о необходимости перезагрузки, выполните `/reload-plugins`. Скиллы плагина вызываются вручную как `/home-project-control:<имя-скилла>` и также выбираются Claude автоматически по `description`.

## Установка из клонированной копии

Если вы хотите хранить исходники локально, клонируйте репозиторий в постоянную папку отдельно от пользовательских проектов:

```text
git clone https://github.com/DmitriyVOstapenko/home-project-control.git
```

Из корня клонированного репозитория добавьте локальный marketplace и установите плагин в нужном клиенте.

Codex:

```text
codex plugin marketplace add .
codex plugin add home-project-control@home-project-control-marketplace
```

Claude Code:

```text
claude plugin marketplace add .
claude plugin install home-project-control@home-project-control-marketplace
```

Не клонируйте отдельную копию плагина внутрь каждого строительного проекта.

## Создание пользовательского проекта

Для каждого дома, квартиры, ремонта или группы связанных локаций и оборудования:

1. создайте или выберите отдельную папку;
2. откройте её как рабочую папку новой задачи Codex или сессии Claude Code;
3. попросите «Контроль дома» начать или продолжить проект;
4. сначала получите результат проверки папки;
5. если структуры ещё нет, подтвердите точный путь и показанный предварительный план создания.

Плагин создаёт служебную папку `.home-control` и тематические каталоги только после подтверждения. Существующие документы не перезаписываются.

Новые документы можно прикреплять к сообщениям с кратким описанием. Плагин покажет, в какие тематические папки он предлагает их скопировать, и применит план только после отдельного подтверждения.

Один установленный плагин можно использовать в нескольких отдельных папках проектов. Внутри одного проекта можно описать одну или несколько площадок, их зоны, системы и конкретные экземпляры установленного оборудования.

## Обновление

Для установки из GitHub в Codex обновите снимок marketplace и переустановите актуальную версию:

```text
codex plugin marketplace upgrade home-project-control-marketplace
codex plugin add home-project-control@home-project-control-marketplace
```

После обновления начните новую задачу Codex.

В Claude Code обновите marketplace и установленный плагин:

```text
claude plugin marketplace update home-project-control-marketplace
claude plugin update home-project-control@home-project-control-marketplace
```

После обновления выполните `/reload-plugins`, если Claude Code предложит это.

Для локальной клонированной копии сначала выполните `git pull`, затем повторите установку плагина из локального marketplace.

Ручное копирование файлов в каталог кэша установленного плагина не используется.

## Удаление

Codex:

```text
codex plugin remove home-project-control@home-project-control-marketplace
```

Claude Code:

```text
claude plugin uninstall home-project-control@home-project-control-marketplace
```

Удаление плагина не должно удалять отдельные пользовательские папки и данные `.home-control`.
