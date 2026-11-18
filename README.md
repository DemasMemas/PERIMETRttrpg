# Периметр

Веб-платформа для проведения настольной ролевой игры в постапокалиптическом сеттинге. «Периметр» объединяет глобальную карту, подлокации, персонажей, инвентарь, боевую систему и инструменты ведущего в одной игровой комнате.

## Возможности

- регистрация, авторизация и игровые комнаты по коду приглашения;
- роли ведущего и игроков, права доступа к персонажам;
- глобальная карта с маркерами, погодой и редактированием;
- трёхмерные подлокации со структурами, контейнерами и предметами на земле;
- перемещение с построением маршрута, препятствиями и перелезанием;
- пошаговый бой с инициативой, ОД, СД и ОП;
- лист персонажа, зоны здоровья, травмы, кровотечения и эффекты;
- инвентарь, экипировка, магазины, патроны и расходники;
- обмен предметами между инвентарём и контейнерами;
- синхронизация состояния комнаты через Socket.IO;
- глобальные и локальные шаблоны предметов для ведущего.

Проект находится в активной разработке. Правила и интерфейсы могут меняться вместе с развитием игровой системы.

## Технологии

- Python 3.13;
- Flask, Flask-SQLAlchemy и Flask-Migrate;
- PostgreSQL;
- Flask-SocketIO;
- JavaScript без frontend-фреймворка;
- Three.js и Howler.js, загружаемые через CDN.

Для проверки производительности графики есть [бенчмарк](docs/GRAPHICS_BENCHMARK.md).

## Быстрый запуск

### 1. Клонирование и окружение

```bash
git clone https://github.com/DemasMemas/PERIMETRttrpg.git
cd PERIMETRttrpg
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Linux или macOS:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. PostgreSQL

Создайте пустую базу и отдельного пользователя. Например:

```sql
CREATE USER perimetr WITH PASSWORD 'change-me';
CREATE DATABASE perimetr OWNER perimetr;
```

### 3. Переменные окружения

Создайте локальный файл `.env`:

```dotenv
FLASK_ENV=development
FLASK_DEBUG=1
SECRET_KEY=replace-with-a-random-secret
JWT_SECRET_KEY=replace-with-another-random-secret
DATABASE_URL=postgresql://perimetr:change-me@localhost/perimetr
```

Файл `.env` содержит секреты и не должен попадать в Git. В production обязательно используйте уникальные длинные значения ключей и отдельные реквизиты базы данных.

### 4. Схема базы данных

```bash
flask --app run.py db upgrade
```

### 5. Запуск

```bash
python run.py
```

После запуска интерфейс доступен по адресу `http://127.0.0.1:5000`.

Для загрузки Three.js, Howler.js и клиентской библиотеки Socket.IO браузеру требуется доступ к интернету.

### Запуск через Radmin VPN

Для закрытого тестирования с другими игроками можно запустить приложение в
виртуальной локальной сети Radmin VPN.

Один раз откройте PowerShell от имени администратора и разрешите входящие
подключения только через адаптер Radmin VPN:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-radmin-firewall.ps1
```

После этого обычный запуск сервера для игры выполняется без прав администратора:

```powershell
.\run-lan.cmd
```

Сценарий применяет миграции, отключает отладочный режим, запускает сервер на всех
локальных интерфейсах и выводит готовый адрес вида
`http://26.x.x.x:5000`. Все игроки должны состоять в одной сети Radmin VPN.
Компьютер ведущего, Radmin VPN, PostgreSQL и окно сервера должны оставаться
запущенными на протяжении сессии.

`run-lan.cmd` также можно запустить двойным щелчком. Он обходит запрет запуска
локальных PowerShell-сценариев только для текущего процесса и не меняет системную
политику Windows.

Если миграции уже применены и их нужно пропустить:

```powershell
.\run-lan.cmd -SkipMigrations
```

## Импорт снаряжения

Служебные скрипты принимают путь к книге `.xlsx` первым аргументом:

```bash
python scripts/import_equipment.py "path/to/Снаряжение.xlsx"
python scripts/import_consumables.py "path/to/Снаряжение.xlsx"
```

Импорт обновляет глобальные шаблоны в подключённой базе данных. Перед импортом рабочих данных рекомендуется сделать резервную копию БД.

## Тесты

Тестовый набор включает unit-тесты игровой логики и интеграционные проверки HTTP API в изолированной SQLite-базе. Установите зависимости разработчика:

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Запуск с отчётом о покрытии:

```bash
python -m pytest tests --cov=app --cov-report=term-missing
```

В репозитории настроен GitHub Actions workflow: при каждом `push` и `pull request` он запускает Python-тесты, контролирует минимальное покрытие и проверяет синтаксис основных JavaScript-модулей.

Проверка синтаксиса основных JavaScript-модулей:

```bash
node --check app/static/js/characterSheet.js
node --check app/static/js/locationScene.js
node --check app/static/js/effects.js
```

## Структура проекта

```text
app/
  auth/             авторизация и профиль пользователя
  lobbies/          HTTP API игровых комнат
  models/           модели SQLAlchemy
  schemas/          схемы валидации
  services/         игровая и доменная логика
  sockets/          события реального времени
  static/           JavaScript, стили и игровые ресурсы
  templates/        HTML-шаблоны
migrations/         миграции базы данных
scripts/            импорт и обслуживание данных
tests/              автоматические проверки правил
run.py              точка запуска приложения
```

## Работа с миграциями

После изменения моделей создайте и проверьте новую миграцию:

```bash
flask --app run.py db migrate -m "Краткое описание изменения"
flask --app run.py db upgrade
```

Файлы из `migrations/versions` должны добавляться в репозиторий вместе с изменениями моделей.
