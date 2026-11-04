# Подготовка Hyper-V к подключению HV Director

Сервер HV Director (Debian 13) управляет узлами Hyper-V через **WinRM / PowerShell Remoting**,
пишет ISO-образы на **SMB-шару** и открывает консоль ВМ через **guacd → RDP (vmconnect) на порт 2179**
того узла, где ВМ работает в данный момент. Все адреса и учётные данные хранятся в PostgreSQL
(пароли зашифрованы мастер-ключом из `/etc/hvdirector/hvdirector.env`).

```
HV Director ──WinRM 5985/5986──▶ каждый узел Hyper-V (PowerShell: Hyper-V, FailoverClusters)
           ──SMB 445──────────▶ \\shared\iso (запись загруженных ISO)
           ──TCP 2179─────────▶ узел-владелец ВМ (консоль, RDP vmconnect)
узлы Hyper-V ──SMB 445─────────▶ \\shared\iso (подключение ISO к ВМ)
```

## 1. Сервисная учётная запись

Рекомендуется доменная учётная запись, например `CORP\svc-hvdirector`:

* член локальной группы **Administrators** на каждом узле (нужны Hyper-V-командлеты, создание
  папок и VHDX на CSV, WinRM-доступ);
* полный доступ к кластеру:
  ```powershell
  Grant-ClusterAccess -User CORP\svc-hvdirector -Full
  ```
* для консоли нужен член группы **Hyper-V Administrators** (или Administrators). В настройках кластера
  в портале можно указать отдельную учётную запись только для консоли.

## 2. WinRM на узлах

```powershell
Enable-PSRemoting -Force
# Разрешить WinRM только с сервера HV Director (пример: 10.0.0.50)
New-NetFirewallRule -DisplayName "HVDirector WinRM" -Direction Inbound -Protocol TCP `
    -LocalPort 5985,5986 -RemoteAddress 10.0.0.50 -Action Allow
```

* **HTTP 5985 + NTLM** (по умолчанию в мастере) работает по IP-адресам и не требует DNS/Kerberos на
  Linux: трафик шифруется на уровне сообщений WinRM (NTLM sealing).
* **HTTPS 5986** — создайте listener с сертификатом узла:
  ```powershell
  $cert = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My
  New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * -CertificateThumbPrint $cert.Thumbprint -Force
  ```
  Для самоподписанных сертификатов снимите флажок «Проверять сертификат».
* **Kerberos** — требует FQDN вместо IP, DNS и `/etc/krb5.conf` на сервере HV Director, а также
  установки `pypsrp[kerberos]` в виртуальное окружение (`/opt/hvdirector/venv/bin/pip install 'pypsrp[kerberos]'`).
* Узлы в рабочей группе (без домена) с локальной учётной записью:
  ```powershell
  New-ItemProperty -Path HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System `
      -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force
  ```

## 3. Консоль ВМ (порт 2179)

Консоль — это RDP-подключение guacd к узлу-владельцу ВМ на порт **2179** в режиме `vmconnect`
(идентификатор ВМ передаётся в preconnection BLOB), так же как работает `vmconnect.exe`.

```powershell
New-NetFirewallRule -DisplayName "HVDirector VM console" -Direction Inbound -Protocol TCP `
    -LocalPort 2179 -RemoteAddress 10.0.0.50 -Action Allow
```

Раскладка клавиатуры сервера настраивается в «Настройки → Консоль ВМ» (по умолчанию `en-us-qwerty`).
guacd не имеет русской серверной раскладки: переключайте раскладку внутри гостевой ОС, а в браузере
держите английскую.

## 4. Виртуальный коммутатор и VLAN

На **всех** узлах создайте внешний коммутатор с одинаковым именем (его выбирают в мастере), подключённый
к trunk-порту физической сети, где заранее созданы VLAN тенантов:

```powershell
New-VMSwitch -Name "vSwitch-Tenants" -NetAdapterName "Trunk" -AllowManagementOS $false
```

HV Director переводит адаптер ВМ в режим access нужного VLAN:
`Set-VMNetworkAdapterVlan -Access -VlanId <VLAN сети>`.

Виртуальные раутеры используют тот же коммутатор: LAN-адаптер раутера работает в режиме trunk
(`Set-VMNetworkAdapterVlan -Trunk -AllowedVlanIdList ...`), WAN и management — в режиме access VLAN внешних сетей.
VLAN внешних сетей (WAN, OOB management) тоже должны приходить на trunk-порты узлов. Подробнее — [routers.md](routers.md).

## 5. Хранилища (CSV / SMB3)

* В кластере используйте Cluster Shared Volumes (`C:\ClusterStorage\VolumeN`) или SMB3-шару
  Scale-Out File Server. Мастер находит CSV автоматически; каждому хранилищу назначьте **класс**
  (Gold / Silver / …) — квоты организаций задаются по классам.
* Файлы ВМ: `<хранилище>\HVDirector\<код организации>\<код организации>_<имя ВМ>\`.
* Для HA-кластера локальные тома не подходят (ВМ должна лежать на общем хранилище).

## 6. Шара ISO-образов `\\shared\iso`

1. Создайте шару на файловом сервере.
2. Права (Share + NTFS):
   * учётная запись загрузки (указывается в «Настройки → Шара ISO») — **Modify**;
   * компьютерные учётные записи узлов Hyper-V (`CORP\HV01$`, … или группа с ними) — **Read**.
3. Если сервер управления не разрешает имя `shared` через DNS, укажите его IP в поле
   «Адрес SMB-сервера для сервера управления» — узлы при этом продолжают использовать `\\shared\iso`.
4. Кнопка «Проверить» записывает и удаляет пробный файл.

Имя загружаемого файла получает GUID-префикс: `3f2c…-…_debian-13.iso`, поэтому образы разных
организаций никогда не конфликтуют.

### Двойной переход (double hop)

Подключение ISO выполняется в удалённой сессии на узле. Если `Set-VMDvdDrive` с UNC-путём завершается
ошибкой «Access denied», значит, узлу не хватает прав на шару или сессии не делегируются учётные данные.
Варианты:

* выдать компьютерным учётным записям узлов права на шару (п. 2) — достаточно в большинстве случаев;
* включить **CredSSP** (в портале: Кластер → Параметры подключения → CredSSP):
  ```powershell
  Enable-WSManCredSSP -Role Server -Force   # на каждом узле
  ```
* настроить Kerberos constrained delegation (служба `cifs` файлового сервера) для компьютерных
  учётных записей узлов.

## 7. Графики загрузки ВМ и изменение дисков

* Раз в минуту HV Director опрашивает каждый узел скриптом `vm_metrics.ps1`:
  * CPU — счётчики гипервизора через CIM-класс `Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor`
    (имена классов не локализуются, работает и на русской Windows Server);
  * память — `MemoryAssigned` / `MemoryDemand` из `Get-VM`;
  * диск и сеть — **Hyper-V resource metering** (`Measure-VM`). Метеринг включается автоматически
    (`Enable-VMResourceMetering`) у включённых ВМ HV Director; для сети используются ACL с действием `Meter`
    (`0.0.0.0/0`, `::/0`), которые не ограничивают трафик.
* Отдельных прав, кроме членства сервисной учётной записи в Administrators узла, не нужно.
* Расширение диска — `Resize-VHD` (только увеличение, VHDX). Диски данных подключены к SCSI и расширяются /
  удаляются у включённой ВМ; системный IDE-диск ВМ поколения 1 — только у выключенной. Пока у ВМ есть
  контрольные точки (снапшоты), диски не меняются. Раздел внутри гостевой ОС расширяет владелец ВМ.
* Память включённой ВМ меняется «на лету» (runtime memory resize, Windows Server 2016+): гостевая ОС должна
  это поддерживать, иначе задача завершится ошибкой и изменение будет отменено.
* Для дисков на SMB3-шаре (Scale-Out File Server) операции `New-VHD`/`Resize-VHD` выполняются в удалённой
  сессии и упираются в двойной переход так же, как ISO (см. п. 6): нужны CredSSP или Kerberos constrained
  delegation (служба `cifs` файлового сервера).

## 8. Проверка из PowerShell

```powershell
Test-WSMan -ComputerName hv01 -Authentication Negotiate -Credential CORP\svc-hvdirector
Invoke-Command -ComputerName hv01 -Credential CORP\svc-hvdirector -ScriptBlock { Get-VMHost; Get-ClusterNode }
# источники графиков загрузки
Invoke-Command -ComputerName hv01 -Credential CORP\svc-hvdirector -ScriptBlock {
    Get-CimInstance Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor | Select-Object -First 3 Name, PercentTotalRunTime
    $vm = Get-VM | Select-Object -First 1; Enable-VMResourceMetering -VM $vm; Measure-VM -VM $vm | Format-List *
}
```
