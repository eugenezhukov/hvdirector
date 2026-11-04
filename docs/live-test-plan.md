# План тестирования HV Director на живом кластере Hyper-V

Сценарий: проект берётся с GitHub на машину с **Debian 13**, работа ведётся в **Claude Code**, проверки идут против
**настоящего кластера Hyper-V**. Этапы Э0–Э15 выполняются по порядку: каждый следующий опирается на предыдущий.

Перед стартом прочитайте [HANDOVER.md](../HANDOVER.md): там состояние проекта, карта кода и известные риски
(раздел 6). Подготовка узлов описана в [hyperv-setup.md](hyperv-setup.md), раутеры — в [routers.md](routers.md).

---

## 0. Правила

**Роли.**
* **Вы** работаете в Hyper-V, AD, сети и браузере и вводите пароли.
* **Claude Code** выполняет команды на Debian, проверяет через CLI, API и БД, читает логи, исправляет код и коммитит.

**Приоритеты.** P1 обязательно для приёмки, P2 желательно, P3 по возможности.

**Безопасность.**
* Работайте только в **тестовой организации** (ниже — код `qa`, имена ВМ в Hyper-V будут `qa_<имя>`) и с тестовыми ВМ.
  Не запускайте скрипты, меняющие состояние (`vm_create`, `vm_reconfigure`, `vm_delete`…), через `hv-run` на рабочих ВМ.
* Отказ узла (Э9.4) проверяйте только на тестовом кластере или в окно обслуживания.
* **Не вводите пароли в чат Claude Code.** Учётные данные вводятся в портал (там они шифруются) или в своём терминале
  через `read -s` до запуска `claude`. Claude Code берёт сохранённые учётки сам: `hv-run --cluster …`.

**Результаты.** Записывайте в `docs/live-test-results.md` (шаблон в конце). Цикл при сбое: запись → исправление →
повтор шага → коммит.

---

## 1. Что подготовить заранее

| Что | Требования |
|---|---|
| Кластер Hyper-V | Windows Server 2019/2022/2025, Failover Clustering, ≥ 2 узлов, CSV. Для проверки «отдельного хоста» — желательно отдельный узел вне кластера (P3) |
| Сеть ВМ | vSwitch **с одинаковым именем** на всех узлах, подключён к trunk; 2–3 тестовых VLAN с маршрутизацией (или без — для L2-проверок) |
| Учётная запись | `CORP\svc-hvdirector`: Administrators на узлах, `Grant-ClusterAccess -Full`, Hyper-V Administrators (консоль) |
| Шара ISO | `\\fileserver\iso`: учётка записи (Modify), компьютерные учётки узлов (Read) |
| ISO | Windows Server 2022 (evaluation) и Debian 13/Ubuntu для гостевых ОС; **Debian 13 netinst или DVD-1** для раутера |
| Для раутеров (Э13) | VLAN **WAN** (подсеть, шлюз, несколько свободных адресов), VLAN **OOB management** (подсеть, шлюз), маршрут с машины Debian в OOB-сеть (SSH 22), доступ из OOB к зеркалу Debian или прокси (для netinst) |
| Машина Debian 13 | 2 vCPU, 4 ГБ RAM, 20 ГБ диска; сетевой доступ к узлам: WinRM 5985/5986, RDP-консоль 2179, SMB 445 к шаре; браузер (или SSH-туннель) |

---

## Э0. Debian 13: подготовка машины и Claude Code (P1, ~30 мин)

```bash
sudo apt update
sudo apt install -y git curl ca-certificates python3 python3-venv postgresql xorriso nodejs npm netcat-openbsd tmux
```

Установите Claude Code по официальной инструкции: нативный установщик `curl -fsSL https://claude.ai/install.sh | bash`
или `npm install -g @anthropic-ai/claude-code`. Затем выполните вход: `claude` → `/login`.

```bash
git clone https://github.com/eugenezhukov/hvdirector && cd hvdirector
git checkout claude/wonderful-darwin-x5yd2d     # или main, если ветка уже слита
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'
sudo -u postgres psql -c "CREATE ROLE hvdirector LOGIN PASSWORD 'hvdirector'"
sudo -u postgres createdb -O hvdirector hvdirector
sudo -u postgres createdb -O hvdirector hvdirector_test
mkdir -p ~/hvd/data
cat > ~/hvd/dev.env <<EOF
HVD_DATABASE_URL=postgresql+psycopg://hvdirector:hvdirector@127.0.0.1:5432/hvdirector
HVD_SECRET_KEY=$(backend/.venv/bin/hvdirector-manage gen-key)
HVD_HYPERV_DRIVER=psrp
HVD_COOKIE_SECURE=false
HVD_LOG_LEVEL=INFO
HVD_DATA_DIR=$HOME/hvd/data
EOF
chmod 600 ~/hvd/dev.env
```

Перед запуском `claude` экспортируйте переменные в том же терминале, чтобы Claude Code их унаследовал:

```bash
export HVD_CONFIG_FILE=~/hvd/dev.env
read -rsp 'Пароль svc-hvdirector: ' HVD_HV_PASSWORD; export HVD_HV_PASSWORD; echo
claude
```

Первое сообщение Claude Code:
> Прочитай CLAUDE.md, HANDOVER.md и docs/live-test-plan.md. Создай docs/live-test-results.md по шаблону из плана.
> Выполни этап Э1 и доложи результат.

| # | Проверка | Ожидается |
|---|---|---|
| 0.1 | `backend/.venv/bin/hvdirector-manage --help` | список команд, в том числе `hv-run` |
| 0.2 | `psql -h 127.0.0.1 -U hvdirector hvdirector -c 'select 1'` (пароль `hvdirector`) | `1` |

---

## Э1. Автотесты на Debian 13 (P1, ~15 мин)

```bash
cd backend && .venv/bin/python -m pytest -q                       # обычный пользователь
sudo env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_router_conf.py tests/test_router_dhcp.py          # root: сетевые namespace
```

| # | Проверка | Ожидается |
|---|---|---|
| 1.1 | Все тесты без root | `passed`; тесты, которым нужен root, — `skipped` |
| 1.2 | Тесты под root | все прошли, **ни одного skipped** |
| 1.3 | `test_live_networkd_dhcp_server` под root | прошёл. **Это первая проверка DHCP-конфигурации на systemd-networkd 257** (той же версии, что на раутере). При падении — исправить до Э13 |

Если `test_live_networkd_dhcp_server` пропущен, проверьте наличие `/usr/lib/systemd/systemd-networkd` и `dbus-daemon`.
Первый входит в пакет `systemd`; если в вашей сборке он вынесен в отдельный пакет — установите его. Второй — пакет `dbus`.

**1.4 (P2) — VRF и VLAN без Hyper-V.** В песочнице разработки модулей VRF и 802.1Q не было, а на Debian 13 они есть.
Задание для Claude Code:
> Напиши тест backend/tests/test_router_vrf.py (пропуск без root и без модулей vrf и 8021q). В network namespace
> собери раскладку раутера: `vrf-tenant` (таблица 100), `lan0` (veth) с VLAN-интерфейсом `vl100`, `wan0` в VRF,
> `mgmt0` в глобальной таблице. Загрузи `render_nftables()` без замен. Проверь:
> * SNAT и DNAT через VRF;
> * изоляцию mgmt0;
> * что DHCP-запрос из VLAN 100 принимается правилом `meta sdifname`, а из `wan0` — нет (networkd с файлом
>   `44-hvd-vl100.network` из `render_networkd()`, клиент — как в test_router_dhcp.py).

Это закрывает главный непроверенный риск раутеров до установки ВМ.

---

## Э2. Подготовка Hyper-V (P1, вы)

Выполните [hyperv-setup.md](hyperv-setup.md), разделы 1–6, затем проверьте на любом узле (PowerShell от администратора):

```powershell
Get-Cluster; Get-ClusterNode | ft Name,State
Get-ClusterSharedVolume | ft Name,State,@{n='Path';e={$_.SharedVolumeInfo.FriendlyVolumeName}}
Get-VMSwitch | ft Name,SwitchType                       # на всех узлах одно и то же имя
winrm enumerate winrm/config/listener                   # HTTP 5985 (и HTTPS 5986, если нужен)
Get-NetFirewallRule -DisplayName 'HVDirector*' | ft DisplayName,Enabled
Get-ClusterAccess                                        # svc-hvdirector: Full
Get-CimInstance Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor | select -First 3 Name,PercentTotalRunTime
```

Последняя команда должна вернуть строки вида `ИмяВМ:Hv VP 0`. Если класса нет — отметьте в журнале: тогда графики CPU
будут работать по более грубому `Get-VM CPUUsage` (HANDOVER §6.1).

---

## Э3. Связь Debian → Hyper-V (P1, Claude Code)

```bash
nc -zv <узел1> 5985; nc -zv <узел1> 2179; nc -zv <файловый-сервер> 445
M=backend/.venv/bin/hvdirector-manage; U='CORP\svc-hvdirector'
$M hv-run ping         --address <узел1> --username "$U"
$M hv-run host_info    --address <узел1> --username "$U"
$M hv-run discover     --address <адрес кластера> --username "$U"
$M hv-run cluster_info --address <адрес кластера> --username "$U"
```

| # | Проверка | Ожидается |
|---|---|---|
| 3.1 | `ping` на каждом узле | `{"name": "<узел>", "hyperv": true, "ps_version": "5.1…"}` |
| 3.2 | `host_info` | память, логические CPU, список коммутаторов и томов |
| 3.3 | `discover` по адресу кластера | `is_cluster: true` |
| 3.4 | `cluster_info` | все узлы с IP и все CSV с путями `C:\ClusterStorage\…` |
| 3.5 (P2) | `--transport https --port 5986` (и `--auth kerberos` с FQDN, если используется) | как 3.1 |

Типовые ошибки:

| Ошибка | Причина и что делать |
|---|---|
| `CONNECT_FAILED` … refused / timeout | нет listener WinRM или правила брандмауэра (`-RemoteAddress` = IP машины Debian), маршрут |
| `AUTH_FAILED` / 401 | формат `DOMAIN\user`, пароль, NTLM запрещён политикой (`--auth negotiate`), локальная учётка без `LocalAccountTokenFilterPolicy` |
| `PS_ERROR … [line N: Get-Cluster…]` | нет прав на кластер (`Grant-ClusterAccess`) или модуля FailoverClusters. Строку N ищите в `build_script('<имя>')` (HANDOVER §4) |
| `WINRM_FAULT` | ограничения WinRM (`MaxEnvelopeSizekb`, квоты оболочки) — смотреть текст ошибки |

Если скрипт ошибается в данных (не те поля, пустые списки) — это первая порция исправлений. Задание для Claude Code:
> `hv-run cluster_info` возвращает … (вставить вывод). Сравни с выводом на узле
> `Get-ClusterNode | fl *` / `Get-ClusterSharedVolume | fl *` (я пришлю) и исправь `cluster_info.ps1`. Обнови
> симулятор, если поменялся формат, прогони pytest, закоммить.

---

## Э4. Запуск портала (P1)

В двух окнах tmux (или в фоне через Claude Code), в каждом — `export HVD_CONFIG_FILE=~/hvd/dev.env`:

```bash
backend/.venv/bin/hvdirector-manage migrate
backend/.venv/bin/hvdirector-manage create-admin --username admin   # пароль вводите сами
make api        # окно 1: http://127.0.0.1:8000
make worker     # окно 2
```

Если браузер на другой машине, откройте туннель: `ssh -L 8000:127.0.0.1:8000 <пользователь>@<debian>`.

Для консоли ВМ соберите и запустите guacd: `sudo bash deploy/build-guacd.sh`, затем в отдельном окне
`/usr/local/sbin/guacd -f -b 127.0.0.1 -l 4822`.

| # | Шаг (портал провайдера) | Ожидается |
|---|---|---|
| 4.1 | Вход `admin` | обзор провайдера |
| 4.2 | Настройки → «Шара ISO-образов»: UNC, учётка, пароль, при необходимости IP сервера → «Проверить» | «Проверка пройдена» |
| 4.3 | Настройки → «Сервер консоли guacd» → «Проверить» | OK |
| 4.4 | `hvdirector-manage check` | PostgreSQL OK, guacd OK |
| 4.5 | Настройки → «Мониторинг ВМ»: 60 с, 14 дней | сохранено |

---

## Э5. Кластер в портале (P1)

| # | Шаг | Ожидается | Сверка |
|---|---|---|---|
| 5.1 | «Классы хранилищ»: `Gold`, `Silver` | созданы | — |
| 5.2 | «Кластеры и узлы» → «Добавить кластер или узел»: адрес кластера, NTLM, учётка; vSwitch; HA включён; CSV → классы | кластер, все узлы Up с IP, CSV с объёмом | `Get-ClusterNode`, `Get-ClusterSharedVolume` |
| 5.3 | Учётка консоли в параметрах кластера (если отличается) | сохранено | — |
| 5.4 | «Опросить (Refresh)» | без ошибок, «изменений нет» | — |
| 5.5 | Через 1–2 мин | у узлов свободная память и число ВМ | `Get-VMHost` |
| 5.6 (P3) | Отдельный хост вне кластера | тип «Отдельный хост», локальные тома | — |

---

## Э6. Организация, сети, ISO (P1)

| # | Шаг | Ожидается |
|---|---|---|
| 6.1 | «Организации» → создать `qa`: кластер, квоты (vCPU 16, RAM 32 ГБ, ВМ 6, снапшоты 5, ISO 20 ГБ, Gold 300 ГБ), VLAN тестового диапазона, администратор `qaadmin` | организация создана |
| 6.2 | Пересечение VLAN с другой организацией | отказ |
| 6.3 | Вход `/tenant/qa/login` | обзор организации, квоты |
| 6.4 | «Сети»: `web` (свободный VLAN, `10.10.1.0/24`, шлюз `.1`), `db` (без адресации) | VLAN занят, повторно не выдаётся |
| 6.5 | ISO: провайдер загружает Debian 13 (общий каталог), тенант — свой ISO | на шаре `<GUID>_имя.iso`, квота ISO учитывается |

---

## Э7. Жизненный цикл ВМ (P1)

Подставьте имя ВМ, например `qa_web01`, и проверяйте в PowerShell на узле-владельце:

```powershell
Get-VM qa_web01 | fl Name,State,Generation,ProcessorCount,MemoryStartup,DynamicMemoryEnabled,Path,ComputerName
Get-VMHardDiskDrive -VMName qa_web01 | ft ControllerType,ControllerNumber,ControllerLocation,Path
Get-VMNetworkAdapter -VMName qa_web01 | ft Name,SwitchName,MacAddress
Get-VMNetworkAdapterVlan -VMName qa_web01
Get-VMFirmware -VMName qa_web01 | fl SecureBoot,SecureBootTemplate,BootOrder
Get-ClusterGroup qa_web01 | fl Name,OwnerNode,State
```

| # | Шаг | Ожидается |
|---|---|---|
| 7.1 | ВМ `web01`: поколение 2, Secure Boot «Windows», 2 vCPU, 4 ГБ, диски 60 ГБ + 20 ГБ (Gold), адаптер в `web`, ISO Windows Server, «включить после создания» | задача успешна; ВМ на узле с наибольшей свободной памятью; роль кластера; диски на SCSI в `…\HVDirector\qa\qa_web01\`; VLAN access = VLAN сети `web` |
| 7.2 | Консоль → установка Windows Server | изображение и клавиатура работают, консоль открывается в новом окне |
| 7.3 | Подключение ISO и двойной переход | если ошибка «Access denied» — права узлов на шару или CredSSP (hyperv-setup.md §6) |
| 7.4 | ВМ `lin01`: поколение 2, Secure Boot «Linux (Microsoft UEFI CA)», Debian 13, сеть `web` | установка проходит |
| 7.5 | ВМ `g1`: поколение 1 | системный диск на IDE 0:0, DVD на IDE 1:0 |
| 7.6 | Питание: завершение работы ОС, включение, перезагрузка ОС, выключение питания, сброс | состояние в портале совпадает с Hyper-V Manager через ≤ 1 мин |
| 7.7 | Снапшот → откат → удаление | `Get-VMSnapshot` совпадает с порталом |
| 7.8 | IP-адреса гостя на вкладке «Общие» (нужны службы интеграции) | адреса видны |
| 7.9 | Удаление `g1` | ВМ, роль кластера и папка удалены |

---

## Э8. Изменение параметров ВМ (P1)

Пути дисков смотрите в БД:

```bash
PGPASSWORD=hvdirector psql -h 127.0.0.1 -U hvdirector hvdirector -c \
  "select v.name,d.position,d.size_gb,d.path from vm_disks d join vms v on v.id=d.vm_id where v.name='web01'"
```

| # | Шаг (вкладка «Оборудование») | Ожидается | Сверка |
|---|---|---|---|
| 8.1 | `web01` включена (Windows Server): память 4 → 6 ГБ | без перезагрузки | `Get-VM qa_web01 \| ft MemoryStartup,MemoryAssigned` |
| 8.2 | То же для гостя без поддержки (например, ВМ во время установки ОС) | задача с ошибкой Hyper-V, значение в портале вернулось, квота не изменилась | — |
| 8.3 | vCPU у включённой ВМ | поле недоступно, подсказка | — |
| 8.4 | Выключить → vCPU 2 → 4 → включить | `ProcessorCount = 4` | `Get-VM` |
| 8.5 | Расширить системный диск 60 → 80 ГБ у включённой ВМ | задача успешна; в гостевой ОС расширить раздел | `Get-VHD <path> \| ft Size` |
| 8.6 | Расширить диск данных 20 → 30 ГБ | то же | `Get-VHD` |
| 8.7 | Снапшот → попытка расширить → удалить снапшот | отказ «удалите снапшоты» | — |
| 8.8 | Удалить диск данных у включённой ВМ | диск отключён горячо, файл удалён, квота уменьшилась | `Get-VMHardDiskDrive`, `Test-Path <path>` |
| 8.9 | Добавить диск 10 ГБ (Silver, если есть хранилище) | новый VHDX на SCSI | `Get-VMHardDiskDrive` |
| 8.10 | `g1` (поколение 1) включена: расширить системный диск | недоступно с подсказкой; у выключенной — работает | — |
| 8.11 | Добавить адаптер в `db`, сменить сеть адаптера, удалить адаптер у включённой ВМ поколения 2 | горячее добавление и удаление; VLAN меняется | `Get-VMNetworkAdapterVlan` |
| 8.12 | MAC нового адаптера после старта ВМ | в портале реальный MAC, не `000000000000` | `Get-VMNetworkAdapter` |
| 8.13 | Превышение квоты (vCPU, диск) | понятный отказ, ничего не изменилось | — |

---

## Э9. Отказоустойчивость и миграции (P1/P2)

| # | Шаг | Ожидается |
|---|---|---|
| 9.1 | Live migration `qa_web01` в Failover Cluster Manager | через ≤ 1 мин портал показывает новый узел; питание и консоль работают |
| 9.2 | Quick migration | то же |
| 9.3 | Пауза узла с вытеснением (Drain) | ВМ переехали, портал их нашёл |
| 9.4 (P2, тестовый кластер) | Жёсткое выключение узла с ВМ | кластер перезапускает ВМ на другом узле, портал находит её; узел помечен недоступным |
| 9.5 | Флаг «обслуживание» на узле в портале → создать ВМ | ВМ не размещается на этом узле |

---

## Э10. Графики загрузки (P1)

Сначала проверьте источник данных:

```bash
backend/.venv/bin/hvdirector-manage hv-run vm_metrics --cluster "<кластер>" --host <узел> \
  --args '{"ids": ["<GUID ВМ из вкладки «Общие» у провайдера>"]}'
```

| # | Шаг | Ожидается |
|---|---|---|
| 10.1 | Вывод `vm_metrics` | `cpu.run`, `cpu.n`, `disk.read_mb`, `net.rx_mb` заполнены, `errors` пуст. Если нет — HANDOVER §6.1–6.2, исправить скрипт |
| 10.2 | Через 2–3 мин после старта ВМ: вкладка «Мониторинг» | все пять графиков с данными, плитки «сейчас» |
| 10.3 | Нагрузка CPU в госте: Windows `1..4 \| % { Start-Job { while ($true) {} } }`, Linux `stress-ng --cpu 2 --timeout 300` | рост на графике CPU, сходится с диспетчером задач гостя (±10 п. п.) |
| 10.4 | Нагрузка диска: `diskspd -c2G -d120 -w50 -t2 -o8 -b64K C:\t.dat` или `fio --name=t --size=2G --rw=randrw --bs=64k --direct=1 --runtime=120 --time_based` | МБ/с и IOPS одного порядка с отчётом утилиты |
| 10.5 | Сеть: `iperf3` между `web01` и `lin01` или копирование большого файла | Мбит/с одного порядка с `iperf3` |
| 10.6 | Память | при статической памяти «Выделено» и подсказка; если Hyper-V сообщает потребность — линия «Потребность гостя» |
| 10.7 | Live migration во время нагрузки | один пропущенный интервал, дальше данные идут |
| 10.8 | Периоды 6 ч / 24 ч на следующий день; таблица | данные за период, пропуски там, где ВМ была выключена |
| 10.9 | `Get-VMNetworkAdapterAcl -VMName qa_web01` | ACL с действием `Meter`; трафик не ограничен |
| 10.10 (P2) | Хост с 30+ ВМ: время одного опроса | лог воркера без таймаутов. Иначе — таймауты в `hyperv/psrp.py` (HANDOVER §6.7) |

---

## Э11. Управление ВМ провайдером (P1)

| # | Шаг | Ожидается |
|---|---|---|
| 11.1 | Провайдер → «Виртуальные машины» → «Действия» → «Включить» / «Завершить работу ОС» | состояние меняется |
| 11.2 | Карточка ВМ: память, vCPU, диск, адаптер | как в Э8; квоты организации соблюдаются |
| 11.3 | Тенант видит задачи провайдера (инициатор `admin`) | в панели задач организации |
| 11.4 | Журнал аудита: `vm.reconfigure`, `vm.power.*` с `by: provider` | записи есть |
| 11.5 | Консоль ВМ раутера из карточки провайдера (после Э13) | открывается |

---

## Э12. Роли и делегирование (P2)

| # | Шаг | Ожидается |
|---|---|---|
| 12.1 | Пользователь `audit` | всё видит, ничего не меняет (кнопки скрыты, API 403) |
| 12.2 | `console` на одну ВМ | видит только её, открывает консоль, не меняет параметры |
| 12.3 | `tenant_admin` на одну ВМ | управляет только этой ВМ |
| 12.4 | Блокировка после N неудачных входов (Настройки → «Безопасность»), разблокировка флажком в карточке пользователя | работает |

---

## Э13. Виртуальный раутер: firewall, NAT, DHCP (P1, если раутеры нужны в первой версии)

**Подготовка (провайдер):**
1. «Внешние сети»: WAN (VLAN, подсеть, шлюз, DNS) и OOB management (VLAN, подсеть, шлюз).
2. Организация `qa` → «Внешние адреса»: 1 management IP и 2–3 WAN-адреса.
3. Настройки → «Виртуальные раутеры»:
   * ISO Debian 13 из общего каталога;
   * зеркало или прокси (для netinst);
   * в «разрешённые источники SSH» — IP машины Debian;
   * в «SSH-ключи администраторов» — ваш публичный ключ.

| # | Шаг | Ожидается | Если нет |
|---|---|---|---|
| 13.1 | Тенант → «Раутер (Edge)» → «Создать раутер» с сетью `web` | установка 5–15 мин, статус «Работает», конфигурация применена | консоль ВМ раутера у провайдера: шаги установщика, preseed, сеть management, зеркало |
| 13.2 | `ssh root@<mgmt-ip>` (ваш ключ) | вход по ключу; `cat /etc/hvd-release` | `mgmt_allowed`, маршрут до OOB |
| 13.3 | На раутере: `ip -br link; ip -br addr; ip vrf show; ip route show vrf vrf-tenant; networkctl list; nft list ruleset` | `mgmt0`, `wan0`, `lan0`, `vl<VLAN>`, `vrf-tenant`; адреса как в портале | `journalctl -u systemd-networkd`, `/usr/local/sbin/hvd-apply` |
| 13.4 | Из `lin01` (сеть `web`, статический IP `.10`): `ping 10.10.1.1` | ответ | — |
| 13.5 | SNAT `10.10.1.0/24` → WAN IP и правило firewall «разрешить исходящий»: `ping 8.8.8.8`, `curl https://deb.debian.org` | работает | `nft list ruleset`, `conntrack -L` |
| 13.6 | DNAT WAN-IP:2222 → `10.10.1.10:22` и правило firewall на `10.10.1.10:22`; `ssh -p 2222` снаружи | вход на `lin01` | — |
| 13.7 | Правило «запретить» выше «разрешить» | трафик блокируется; порядок правил работает | — |
| 13.8 | Режим stateless | ответы проходят только при явном правиле | — |
| 13.9 | Изоляция: из `lin01` ping шлюза OOB и `mgmt-ip` раутера | нет ответа | — |
| 13.10 | DHCP: в сети `web` включить DHCP (пул `.100–.199`, DNS, домен); в `lin01` переключить интерфейс на DHCP | адрес из пула, шлюз `.1`, DNS и домен из настроек | на раутере `tcpdump -ni vl<VLAN> port 67 or port 68`; `nft list chain inet hvd_filter input`; временно `nft insert rule inet hvd_filter input udp dport 67 counter`; `networkctl status vl<VLAN>`. **Если запросы не доходят до сервера, проверить правило `meta sdifname` внутри VRF** (HANDOVER §6.8) |
| 13.11 | Статическая привязка по MAC `lin01` → `.20`, обновить аренду | адрес `.20` | — |
| 13.12 | «Активные аренды» | аренды с MAC, именем ВМ и сроком | `networkctl --json=short status vl<VLAN>` на раутере |
| 13.13 | Подключить к раутеру вторую сеть, отключить, удалить раутер | VLAN-интерфейсы появляются и исчезают; удаление освобождает квоту | — |

---

## Э14. Установка на чистый Debian 13 (P1 перед продуктивом)

Отдельная ВМ Debian 13 (минимальная установка + SSH), доступ к узлам как в разделе 1.

```bash
sudo apt install -y git && git clone https://github.com/eugenezhukov/hvdirector && cd hvdirector
git checkout claude/wonderful-darwin-x5yd2d
sudo ./deploy/install.sh --hostname hvd.lab.local
```

Команды управления на установленной системе:

```bash
sudo -u hvdirector bash -c 'set -a; . /etc/hvdirector/hvdirector.env; set +a; /opt/hvdirector/venv/bin/hvdirector-manage check'
journalctl -u hvdirector-api -u hvdirector-worker -f
```

| # | Шаг | Ожидается |
|---|---|---|
| 14.1 | Вывод установщика | URL, пароль `admin`, путь к мастер-ключу. Сохраните `/etc/hvdirector/hvdirector.env` в надёжное место |
| 14.2 | `systemctl status hvdirector-api hvdirector-worker hvdirector-guacd nginx` | все active |
| 14.3 | `https://hvd.lab.local` (самоподписанный сертификат; имя должно резолвиться через DNS или hosts) | вход, смена пароля |
| 14.4 | Короткий прогон: Э4.2–4.5, Э5.2, Э7.1, Э7.2 (консоль через встроенный guacd), Э10.2 | работает |
| 14.5 | Повторный запуск `sudo ./deploy/install.sh` (обновление) | данные и настройки сохранились, сервисы перезапущены |
| 14.6 | Резервная копия: `pg_dump` + env-файл; восстановление на второй машине с тем же ключом | учётки кластера расшифровываются, портал работает |

Обновление кода на установленной системе после исправлений: `git pull && sudo ./deploy/install.sh`.

---

## Э15. Негативные сценарии и устойчивость (P2)

| # | Шаг | Ожидается |
|---|---|---|
| 15.1 | Неверный пароль в мастере кластера | понятная ошибка «аутентификация» |
| 15.2 | На одном узле `Stop-Service WinRM` на 5 мин | узел недоступен; ВМ на других узлах управляются, графики остальных ВМ идут; после запуска службы всё восстанавливается |
| 15.3 | Недоступная шара ISO | «Проверить» и загрузка завершаются понятной ошибкой |
| 15.4 | Перезапуск воркера во время создания ВМ | задача «Interrupted by worker restart». **Известный пробел:** хук отката не вызывается — ВМ может остаться в статусе «создаётся», резерв квоты не снимается. Зафиксировать поведение и решить, исправлять ли в первой версии |
| 15.5 | Две операции подряд над одной ВМ | выполняются по очереди |
| 15.6 | Удаление ВМ вне портала (Hyper-V Manager) | портал показывает состояние «Неизвестно»; удаление в портале чистит запись и папку |
| 15.7 | Сутки работы под фоновой синхронизацией и сбором метрик | в логах нет необъяснённых ERROR, размер `vm_metrics` растёт ожидаемо |

---

## Цикл исправлений с Claude Code

1. Сбой → скопируйте Claude Code шаг плана (например, «Э8.5») и сообщение задачи из панели задач.
2. Claude Code воспроизводит сбой без портала:

```bash
PGPASSWORD=hvdirector psql -h 127.0.0.1 -U hvdirector hvdirector -c \
  "select kind,status,message,params from tasks order by created_at desc limit 5"
backend/.venv/bin/hvdirector-manage hv-run <скрипт> --cluster "<кластер>" --host <узел> --args '<json>'
```

3. Claude Code правит `hyperv/scripts/<скрипт>.ps1` (и Python-часть). При изменении формата данных правит симулятор
   `hyperv/fake.py`, прогоняет pytest, перезапускает воркер и просит вас повторить шаг в UI.
4. Коммит с номером шага в сообщении, push; строка в журнале результатов.

Примеры заданий:
> Шаг Э7.1 упал: «Создание ВМ» — `PS_ERROR … [line 112: Add-VMHardDiskDrive]`. Найди параметры задачи в tasks,
> собери скрипт через `build_script('vm_create')`, найди строку, исправь. Проверь через hv-run только с тестовым
> именем ВМ `qa_hvrun_test` и удали её после проверки.

> Графики CPU пустые. Выполни `hv-run vm_metrics` для ВМ web01, разбери `errors`. Если класс счётчиков называется
> иначе, найди правильный на узле (я выполню команды, которые ты дашь), исправь `vm_metrics.ps1`, добавь тест в
> `test_vm_hardware.py`.

---

## Критерии приёмки

* Все шаги P1 выполнены, либо отклонения записаны и приняты.
* В логах API и воркера за сутки работы (Э15.7) нет необъяснённых ошибок.
* Каждое исправление закоммичено; изменения поведения Hyper-V отражены в симуляторе; тесты зелёные.
* Обновлены HANDOVER.md §6 (снятые риски) и документация, если менялось поведение.

---

## Шаблон журнала `docs/live-test-results.md`

```markdown
# Результаты тестирования на живом кластере

Стенд: кластер <имя>, Windows Server <версия>, узлов <N>; Debian 13 <hostname>; ветка/коммит <sha>; дата <…>

| Шаг | Приоритет | Результат | Дата | Примечание | Коммит |
|-----|-----------|-----------|------|------------|--------|
| Э1.2 | P1 | OK | | все тесты под root прошли | |
| Э3.4 | P1 | FAIL→OK | | cluster_info не возвращал IP узлов | abc1234 |
```

Результат: `OK`, `FAIL`, `FAIL→OK` (исправлено), `N/A`, `SKIP` (с причиной).
