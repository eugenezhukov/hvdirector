# HV Director — handover для продолжения работы (Claude Code + живой кластер Hyper-V)

Документ для продолжения разработки и **первого прогона на настоящем кластере Hyper-V** с Linux-машины.
Сначала прочитайте разделы 1–3, затем выполняйте план тестирования из раздела 5 по порядку.

Связанные документы: [README.md](README.md) (возможности, установка), [docs/hyperv-setup.md](docs/hyperv-setup.md)
(подготовка узлов), [docs/routers.md](docs/routers.md) (виртуальные раутеры, DHCP), API — `/api/docs` (OpenAPI).

---

## 1. Состояние проекта

**Что это.** Мультитенантный портал самообслуживания для кластеров Hyper-V (упрощённый аналог VMware Cloud Director):
провайдер подключает кластеры/хосты, классы хранилищ, организации с квотами и пулами VLAN; тенанты создают ВМ, сети,
ISO, снапшоты, открывают консоль, держат виртуальный раутер (Debian 13: firewall, NAT, DHCP). Один сервер Debian 13
без Docker.

**Ветка:** `claude/wonderful-darwin-x5yd2d` в `eugenezhukov/hvdirector`.

| Блок | Сделано | Проверено здесь | Не проверено (нужен живой стенд) |
|---|---|---|---|
| Платформа, БД, API, RBAC, задачи, аудит | да | pytest (48 тестов), Playwright | — |
| Драйвер Hyper-V (pypsrp + PowerShell) | да | синтаксис скриптов, симулятор | **ни один скрипт не выполнялся на настоящем Hyper-V** |
| Мастер кластера, опрос, синхронизация ВМ | да | симулятор | WinRM, `Get-Cluster*`, CSV |
| ВМ: создание, питание, снапшоты, ISO, HA | да | симулятор | `New-VM`, `Add-ClusterVirtualMachineRole`, SMB ISO |
| Изменение ВМ: vCPU, память «на лету», диски (расширение/удаление/добавление), адаптеры | да | симулятор | `Set-VMMemory` у включённой ВМ, `Resize-VHD` онлайн, горячее удаление SCSI-диска |
| Графики загрузки ВМ | да | симулятор, расчёт скоростей, UI | **имена CIM-класса и свойств `Measure-VM`** (раздел 6) |
| Управление ВМ провайдером | да | API-тесты, Playwright | — |
| Консоль (guacd → RDP vmconnect :2179) | да | guacd + VNC в симуляторе | RDP `vmconnect` к настоящему узлу |
| Виртуальные раутеры (Edge), firewall/NAT | да | nftables в netns с реальным трафиком, sshd, xorriso | установка Debian 13 из ISO, **VLAN/VRF** (в песочнице не было модулей ядра) |
| DHCP на раутере | да | настоящий systemd-networkd 255 + DHCP-клиент | правило `meta sdifname` внутри VRF, networkd 257 (Debian 13) |
| Установщик `deploy/install.sh` | да | частично | чистый Debian 13 целиком |

Главный риск: **код, работающий с Hyper-V, проверен только против симулятора** (`HVD_HYPERV_DRIVER=fake`).
Ожидайте ошибки в PowerShell-скриптах при первом прогоне — для их быстрой отладки есть `hvdirector-manage hv-run`
(раздел 4).

---

## 2. Быстрый старт на Linux-машине (режим разработки)

Нужно: Python ≥ 3.11 (проверено на 3.13), PostgreSQL ≥ 14 (графики используют `date_bin`), Node.js 20+ (только для
пересборки UI), `xorriso` (раутеры), сетевой доступ к узлам: WinRM 5985/5986, RDP 2179 (консоль), SMB 445 к шаре ISO,
SSH 22 к management-сети раутеров.

```bash
git clone https://github.com/eugenezhukov/hvdirector && cd hvdirector
git checkout claude/wonderful-darwin-x5yd2d
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'
```

База данных (локальный PostgreSQL):

```bash
sudo -u postgres psql -c "CREATE ROLE hvdirector LOGIN PASSWORD 'hvdirector'"
sudo -u postgres psql -c "CREATE DATABASE hvdirector OWNER hvdirector"
sudo -u postgres psql -c "CREATE DATABASE hvdirector_test OWNER hvdirector"   # для pytest
```

Файл конфигурации `~/hvd/dev.env` (всё остальное — адреса узлов, учётки, шара — хранится в БД):

```
HVD_DATABASE_URL=postgresql+psycopg://hvdirector:hvdirector@127.0.0.1:5432/hvdirector
HVD_SECRET_KEY=<вывод backend/.venv/bin/hvdirector-manage gen-key>
HVD_HYPERV_DRIVER=psrp
HVD_COOKIE_SECURE=false
HVD_LOG_LEVEL=INFO
HVD_DATA_DIR=/home/<user>/hvd/data
HVD_FAKE_STATE=/home/<user>/hvd/fake-hyperv.json
```

```bash
export HVD_CONFIG_FILE=~/hvd/dev.env
backend/.venv/bin/hvdirector-manage migrate
backend/.venv/bin/hvdirector-manage create-admin --username admin
make api      # uvicorn на 127.0.0.1:8000, UI из frontend/dist (собран и закоммичен)
make worker   # в другом терминале: задачи, синхронизация, сбор метрик
```

Портал: `http://127.0.0.1:8000/` (провайдер), `http://127.0.0.1:8000/tenant/<код>/login` (организация).
Для демонстрации без кластера поставьте `HVD_HYPERV_DRIVER=fake` (симулятор; адрес с последним октетом < 200 в мастере
превращается в кластер из трёх узлов).

Консоль ВМ требует guacd 1.6 с FreeRDP 3: `sudo bash deploy/build-guacd.sh` (сборка из исходников в /usr/local,
флаги для FreeRDP 3 уже в скрипте), запуск `/usr/local/sbin/guacd -f -b 127.0.0.1 -l 4822` и адрес guacd в
«Настройки → Сервер консоли guacd». Без guacd всё остальное работает.

Промышленная установка на Debian 13: `sudo ./deploy/install.sh --hostname hvdirector.example.com` (README).

Тесты:

```bash
cd backend && .venv/bin/python -m pytest -q          # нужен PostgreSQL с БД hvdirector_test
sudo .venv/bin/python -m pytest -q tests/test_router_conf.py tests/test_router_dhcp.py   # + тесты в netns (root)
```

UI: `cd frontend && npm ci && npm run dev` (Vite с прокси на :8000) или `npx vite build` — **`frontend/dist` коммитится**
(установщик использует готовую сборку), пересобирайте и коммитьте её вместе с изменениями `frontend/src`.

---

## 3. Архитектура и карта кода

```
Браузер (Vue 3 + Element Plus) ── HTTPS ── nginx ──┬─ frontend/dist
                                                   ├─ /api → hvdirector-api (FastAPI, uvicorn)
                                                   └─ /ws/console?ticket=… (WebSocket) → guacd → RDP vmconnect :2179
hvdirector-worker: очередь задач в PostgreSQL (SKIP LOCKED), опрос ВМ (sync), сбор метрик, housekeeping
Hyper-V: WinRM/PSRP (pypsrp) → PowerShell-скрипты; раутеры: SSH (paramiko) по OOB management
```

| Путь | Что там |
|---|---|
| `backend/hvdirector/models.py` | вся схема БД (SQLAlchemy 2); миграции `migrations/versions/0001…0004` |
| `backend/hvdirector/api/` | `auth`, `common` (задачи, события), `provider` (кластеры, тенанты, настройки), `provider_vms` (питание/параметры/графики ВМ провайдером), `provider_edge` (внешние сети, раутеры), `tenant` (ВМ, сети, ISO, пользователи), `edge` (раутер, firewall, NAT, DHCP), `console` (тикеты, туннель) |
| `backend/hvdirector/services/vmops.py` | обработчики задач ВМ (`@handler("vm.create")` и т. д.) и хуки отката `@on_error` |
| `backend/hvdirector/services/vmconfig.py` | проверки и резервирование квот для изменения ВМ (общие для тенанта и провайдера) |
| `backend/hvdirector/services/inventory.py` | опрос кластера, поиск узла-владельца ВМ, `apply_vm_summary` |
| `backend/hvdirector/services/metrics.py` | сбор метрик, расчёт скоростей, выборка для графиков |
| `backend/hvdirector/services/router*.py` | раутеры: конфигурация ОС (`routerconf`), установщик/preseed (`routerprov`), SSH (`routerssh`), задачи (`routers`) |
| `backend/hvdirector/hyperv/` | `base.py` (интерфейс драйвера), `psrp.py` (WinRM), `fake.py` (симулятор), `scripts/*.ps1` |
| `backend/hvdirector/worker.py` | исполнитель задач и периодические циклы (sync, metrics, housekeeping) |
| `frontend/src/views/provider`, `views/tenant` | страницы порталов; `components/` — `VmHardware`, `VmMetrics`, `LineChart`, `EdgeDhcp`, `ConsoleDialog`… |
| `frontend/src/i18n/ru.js`, `en.js` | все тексты; ошибки API переводятся по коду `errors.<code>` |
| `deploy/` | `install.sh`, `build-guacd.sh`, systemd-юниты, nginx |

Как устроен вызов Hyper-V. Каждый скрипт получает **один JSON-параметр** (`$ArgsJson` → `$A`), значения никогда не
подставляются в текст скрипта. Скрипт возвращает hashtable; обёртка печатает `HVDJSON:{"ok":…,"data":…}`.
Ошибки бизнес-уровня — `Fail 'CODE' 'message'` (→ `HyperVError(code)`), прочие исключения → `PS_ERROR` с номером строки.
Общие функции — `scripts/_common.ps1`, обёртка — `PRELUDE`/`WRAPPER` в `hyperv/psrp.py`.

Задачи. API проверяет запрос, резервирует ресурсы в БД (квоты) и ставит задачу (`services/taskqueue.enqueue`); воркер
выполняет обработчик; при исключении вызывается `@on_error`-хук, который откатывает резерв. Задачи сериализуются по
`lock_key` (например, `vm:<id>`). Статусы: `queued → running → success | error`.

Метрики. Раз в `metrics.interval_s` (60 с) воркер вызывает `vm_metrics.ps1` на каждом узле в состоянии Up; скрипт
возвращает накопительные счётчики, сервис считает средние за интервал по двум соседним замерам (смена узла или сброс
счётчиков пропускает один интервал). Хранение — `vm_metrics` (PK `vm_id, ts`), очистка старше `retention_days`.

---

## 4. Подключение к живому кластеру и отладка

1. Подготовьте узлы по [docs/hyperv-setup.md](docs/hyperv-setup.md): сервисная учётка (Administrators на узлах +
   `Grant-ClusterAccess -Full`), WinRM, порт 2179, одинаковый vSwitch на всех узлах, trunk с VLAN, CSV, шара ISO.
2. Проверьте WinRM **с Linux-машины** до запуска портала:

```bash
HVD_HV_PASSWORD='***' backend/.venv/bin/hvdirector-manage hv-run ping --address 10.0.0.11 --username 'CORP\svc-hvdirector'
backend/.venv/bin/hvdirector-manage hv-run host_info --address 10.0.0.11 --username 'CORP\svc-hvdirector'
backend/.venv/bin/hvdirector-manage hv-run discover --address 10.0.0.10 --username 'CORP\svc-hvdirector'
```

   `--auth ntlm|negotiate|kerberos|credssp|basic`, `--transport https --port 5986`, `--cert-validation`.
   После добавления кластера в портал используйте сохранённые в БД учётные данные:

```bash
backend/.venv/bin/hvdirector-manage hv-run list_vms --cluster "Имя кластера" --host HV01
backend/.venv/bin/hvdirector-manage hv-run vm_info --cluster "Имя кластера" --host HV01 --args '{"id": "<GUID ВМ>"}'
backend/.venv/bin/hvdirector-manage hv-run vm_metrics --cluster "Имя кластера" --host HV01 --args '{"ids": ["<GUID>"]}'
```

   `hv-run` печатает JSON-результат или `{"ok": false, "code": …, "error": …}` — это ровно то, что получает портал.
   Скрипты, которые меняют состояние (`vm_create`, `vm_reconfigure`, `vm_delete`…), запускайте так только на
   тестовых ВМ.
3. Логи: `make api`/`make worker` пишут в консоль (на сервере — `journalctl -u hvdirector-api -u hvdirector-worker`).
   `HVD_LOG_LEVEL=DEBUG` показывает каждый запуск скрипта. Ошибки задач видны в панели задач и в таблице `tasks`
   (`message`, `result`); аудит — таблица `events`.
4. Ошибка в скрипте: правьте `backend/hvdirector/hyperv/scripts/<имя>.ps1`, перезапускать ничего не нужно для
   `hv-run`; для портала перезапустите воркер (скрипты кешируются в процессе, `build_script` с `lru_cache`).
   Сообщение `PS_ERROR` содержит номер строки внутри собранного скрипта (PRELUDE + `_common.ps1` + тело): чтобы найти
   строку, соберите текст `python -c "from hvdirector.hyperv.psrp import build_script; print(build_script('vm_create'))" | cat -n`.

---

## 5. План тестирования на живом кластере (по порядку)

Создайте отдельную организацию для тестов (например, `qa`) с небольшими квотами и 2–3 VLAN из trunk.
После каждого шага смотрите панель задач и, при ошибке, `message` задачи → исправление скрипта → повтор.

| # | Проверка | Ожидаемый результат | Где ломается чаще всего |
|---|---|---|---|
| 1 | Мастер «Кластеры → Добавить»: адрес кластера/узла, NTLM | определён кластер или хост, узлы с IP, CSV, vSwitch | `discover.ps1`, `cluster_info.ps1`: имена свойств `Get-ClusterNode`/`Get-ClusterSharedVolume`, IP узлов |
| 2 | Опрос (Refresh), состояние узлов, «Синхронизация» | узлы Up, память/CPU узлов, без ошибок в логе воркера | `host_info.ps1`, `node_states.ps1` |
| 3 | Классы хранилищ → назначить CSV; шара ISO → «Проверить» | тестовый файл записан и удалён | SMB-учётка, `server` (IP) если нет DNS |
| 4 | Загрузить ISO (провайдер и тенант) | файл `<GUID>_name.iso` на шаре | права записи на шару |
| 5 | Создать ВМ поколения 2 с ISO, сетью, 2 дисками, «включить» | ВМ на узле с наибольшей свободной памятью, роль кластера, VLAN access | `vm_create.ps1` (папки на CSV, `Set-VMFirmware`), `vm_make_ha.ps1`, двойной переход при подключении ISO (CredSSP) |
| 6 | То же для поколения 1 | IDE-диск 0, DVD на IDE 1 | `vm_create.ps1` |
| 7 | Питание: старт, завершение работы ОС, выключение, перезагрузка, сброс | состояния совпадают с Hyper-V Manager | `vm_power.ps1` (`Restart-VM -Type Reboot` есть не везде) |
| 8 | Консоль | картинка ВМ в браузере | guacd ↔ узел:2179, учётка консоли (Hyper-V Administrators), `security=vmconnect` |
| 9 | Снапшот → откат → удаление | состояние совпадает | `vm_snapshot.ps1` |
| 10 | Изменение: память у **включённой** ВМ (Windows Server 2016+ гость) | память меняется без перезагрузки; у неподдерживаемой гостевой ОС — понятная ошибка и откат значения в портале | `vm_reconfigure.ps1`: `Set-VMMemory -StartupBytes` |
| 11 | vCPU у выключенной ВМ; попытка у включённой | меняется / отказ «выключите ВМ» | — |
| 12 | Расширить системный диск у включённой ВМ поколения 2; диск данных; удалить диск данных | `Resize-VHD` онлайн, файл VHDX удалён; квота организации обновилась | `vm_reconfigure.ps1`; у ВМ со снапшотом — отказ |
| 13 | Добавить адаптер (сеть/без сети), сменить сеть, удалить адаптер у включённой ВМ поколения 2 | VLAN access меняется, MAC появляется после старта ВМ | `Set-HvdNicVlan`, `Set-HvdNicSwitch` в `_common.ps1` |
| 14 | Через 2–3 минуты: вкладка «Мониторинг» | графики CPU, памяти, диска, сети не пустые | **раздел 6**: класс `Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor`, свойства `Measure-VM` |
| 15 | Live migration ВМ в Failover Cluster Manager, затем действия в портале | портал находит новый узел, графики пропускают один интервал | `find_owner.ps1`, `list_vms.ps1` |
| 16 | Провайдер: «Виртуальные машины» → карточка ВМ → питание, параметры, графики | задачи видны организации, события в аудите | — |
| 17 | Удаление ВМ | ВМ, роль кластера и папки удалены | `vm_delete.ps1` |
| 18 | Раутер: внешние сети (WAN, OOB), адреса организации, ISO Debian 13 netinst/DVD, «Создать раутер» | установка 5–15 мин, раутер отвечает по SSH, применена конфигурация | preseed (`routerprov.py`), доступ к зеркалу, `Microsoft UEFI CA`, VLAN management |
| 19 | Трафик через раутер: SNAT наружу, DNAT внутрь, firewall stateful/stateless | как в docs/routers.md | VRF/VLAN на Debian 13 (не проверено), `hvd-apply` |
| 20 | DHCP на раутере: включить в сети, ВМ получает адрес; статическая привязка; «Активные аренды» | адрес из пула/привязки, шлюз = раутер, DNS/домен | **правило `meta sdifname`** в input (внутри VRF), `networkctl --json` в networkd 257 |

Проверка установщика на чистом Debian 13 (отдельная ВМ): `sudo ./deploy/install.sh --hostname …`, затем
`hvdirector-manage check`, вход в портал, шаги 1–5.

---

## 6. Известные риски и как их проверить

1. **Графики (CPU).** `vm_metrics.ps1` читает `Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor`
   (`Name` вида `<имя ВМ>:Hv VP 0`, `PercentTotalRunTime`, `Timestamp_Sys100NS`). Проверка на узле:
   `Get-CimInstance Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor | Select -First 3 Name,PercentTotalRunTime,Timestamp_Sys100NS`.
   Если класса нет — скрипт вернёт ошибку в `errors`, а CPU посчитается по `Get-VM CPUUsage` (грубее). Формула —
   `services/metrics.compute_sample`.
2. **Графики (диск, сеть).** `Measure-VM`: `AggregatedDiskDataRead`, `AggregatedDiskDataWritten` (МБ),
   `AggregatedNormalizedIOCount`, `NetworkMeteredTrafficReport[].Direction/TotalTraffic` (МБ). Проверка:
   `Enable-VMResourceMetering -VMName X; Measure-VM -VMName X | Format-List *` и
   `(Measure-VM -VMName X).NetworkMeteredTrafficReport`. Разрешение диска/сети — 1 МБ за интервал.
   Метеринг и ACL `Meter` включаются скриптом автоматически только у ВМ HV Director.
3. **Память (потребность).** `MemoryDemand` у статической памяти может быть 0 — UI тогда показывает выделенную
   память с подсказкой. Если нужна реальная загрузка памяти гостя — Dynamic Memory (не реализовано) или агент в ВМ.
4. **MAC-адреса** обновляются из `Get-HvdVmSummary` (поле `nics`, свойство `$vm.NetworkAdapters`). Динамический MAC
   назначается при первом старте и может смениться после миграции с перезапуском — статические привязки DHCP от
   этого ломаются (в бэклоге: статические MAC для ВМ).
5. **Изменение ВМ.** Порядок в `vm_reconfigure.ps1`: проверки → vCPU → память → расширение дисков → новые диски →
   адаптеры → удаление дисков (последним). Если задача падает на середине, откат в БД вернёт резерв квоты, но уже
   выполненные шаги в Hyper-V останутся (например, расширенный диск) — сверить вручную.
6. **SMB3-хранилища (SOFS).** `New-VHD`/`Resize-VHD` в удалённой сессии упираются в двойной переход — нужен CredSSP
   (параметр подключения кластера) или Kerberos constrained delegation.
7. **Таймауты WinRM.** `PsrpDriver(operation_timeout=20, read_timeout=30)`; при большом числе ВМ на узле сбор метрик
   (Measure-VM на каждую ВМ) может идти долго — увеличить таймауты в `hyperv/psrp.py` или делить список ВМ.
8. **Раутеры.** Не проверены: установка Debian 13 из ISO (preseed, `Microsoft UEFI CA`), VLAN-интерфейсы и VRF,
   правило DHCP `meta sdifname { "vl…" } udp dport 67` (внутри VRF входящий интерфейс — VRF-устройство, а сеть — его
   slave; если DHCP не отвечает — смотреть `nft list ruleset` и счётчики), `networkctl --json=short status` в networkd 257.
9. **Консоль.** Проверена только связка guacd ↔ VNC в симуляторе; для RDP vmconnect важны `security=vmconnect`,
   `preconnection-blob=<VMId>`, порт 2179, учётка консоли (параметры кластера), раскладка (`Настройки → Консоль`).

---

## 7. Соглашения разработки

* Бэкенд: Python 3.11+, FastAPI, SQLAlchemy 2 (sync), ширина строки ~120. Ошибки для пользователя — `AppError(code,
  message, **params)` / `Conflict` / `NotFound`; **каждый новый `code` добавить в `frontend/src/i18n/ru.js` и `en.js`**
  (`errors.<code>`, параметры в `{}`).
* Изменение схемы: поправить `models.py` →
  `hvdirector-manage makemigration -m "..."` → переименовать файл в `000N_<описание>.py` и задать `revision = '000N'`,
  `down_revision` — предыдущая. Проверить, что повторный `makemigration` даёт пустую миграцию (и удалить её).
* Новая операция с Hyper-V: метод в `hyperv/base.py` → реализация в `psrp.py` (скрипт `scripts/<имя>.ps1`) и в
  `fake.py` (симулятор, чтобы работали тесты и демо) → обработчик задачи в `services/` → API → UI → тест.
  `test_units.test_ps_scripts_wellformed` проверяет скобки и обёртку всех скриптов.
* PowerShell: значения только из `$A`; `foreach ($x in $A.list)` без `@()` (иначе `$null` даёт одну итерацию);
  возвращать hashtable; `Fail 'CODE' 'message'` для ожидаемых отказов.
* UI: Vue 3 `<script setup>`, Element Plus, тексты только через i18n (ru и en одновременно). После изменений —
  `npx vite build` и коммит `frontend/dist`.
* Тесты: `backend/tests` (`conftest.py` поднимает схему в `hvdirector_test`, `run_tasks()` выполняет очередь
  синхронно, `Api` — клиент с CSRF). Новая функция — тест через API на симуляторе.
* Секреты: пароли в БД шифруются `HVD_SECRET_KEY` (Fernet). Не логировать пароли; сохранять мастер-ключ вместе с
  резервной копией БД.
* Коммиты: английский, повелительное наклонение, что и зачем.

---

## 8. Бэклог (что дальше)

1. Прогон на живом кластере по разделу 5 и исправления скриптов — **первый приоритет**.
2. Статические MAC-адреса адаптеров ВМ (для DHCP-привязок в кластере).
3. Шаблоны ВМ и клонирование с подготовкой ОС (cloud-init / unattend.xml).
4. Резервное копирование ВМ и самого HV Director (БД + мастер-ключ).
5. Dynamic Memory (квота по максимуму) — даст реальную потребность памяти на графиках.
6. Двухфакторный вход, AD/LDAP/SSO, API-токены.
7. VPN (IPsec/WireGuard), статические маршруты, hairpin NAT на раутере.
8. Режим обслуживания узла с эвакуацией ВМ, anti-affinity, QoS (IOPS, полоса), импорт существующих ВМ, email-уведомления.
