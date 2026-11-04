# Common helpers prepended to every HV Director script.
# Runs under Windows PowerShell 5.1 on Hyper-V hosts (Windows Server 2016+).

function Fail([string]$Code, [string]$Message) {
    throw ("HVD:{0}:{1}" -f $Code, $Message)
}

function To-MB($bytes) {
    if ($null -eq $bytes) { return $null }
    return [int64][math]::Round([double]$bytes / 1MB)
}

function To-GB($bytes) {
    if ($null -eq $bytes) { return $null }
    return [math]::Round([double]$bytes / 1GB, 1)
}

function Get-HvdVm([string]$Id) {
    $vm = $null
    try { $vm = Get-VM -Id $Id -ErrorAction Stop } catch { $vm = $null }
    if (-not $vm) { Fail 'VM_NOT_FOUND' ("VM {0} is not registered on host {1}" -f $Id, $env:COMPUTERNAME) }
    return $vm
}

function Get-HvdVmIps($vm) {
    $ips = @()
    try {
        foreach ($a in $vm.NetworkAdapters) {
            foreach ($ip in $a.IPAddresses) {
                if ($ip -and ($ip -notlike 'fe80*')) { $ips += [string]$ip }
            }
        }
    } catch { }
    return ,$ips
}

function Get-HvdVmSummary($vm) {
    return @{
        id                 = [string]$vm.Id
        name               = [string]$vm.Name
        state              = [string]$vm.State
        cpu_usage          = [int]$vm.CPUUsage
        memory_assigned_mb = (To-MB $vm.MemoryAssigned)
        uptime_s           = [int64]$vm.Uptime.TotalSeconds
        clustered          = [bool]$vm.IsClustered
        ips                = (Get-HvdVmIps $vm)
        host               = $env:COMPUTERNAME
    }
}

function Get-HvdHostInfo {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $vmhost = Get-VMHost
    $switches = @()
    foreach ($s in @(Get-VMSwitch)) { $switches += @{ name = [string]$s.Name; type = [string]$s.SwitchType } }
    $volumes = @()
    try {
        foreach ($v in @(Get-Volume -ErrorAction Stop)) {
            if ($v.DriveLetter -and [string]$v.DriveType -eq 'Fixed') {
                $volumes += @{ path = ("{0}:\" -f $v.DriveLetter); label = [string]$v.FileSystemLabel;
                               size_gb = (To-GB $v.Size); free_gb = (To-GB $v.SizeRemaining) }
            }
        }
    } catch { }
    $addresses = @()
    try {
        foreach ($ip in @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop)) {
            if ($ip.IPAddress -notlike '127.*' -and $ip.IPAddress -notlike '169.254.*') { $addresses += [string]$ip.IPAddress }
        }
    } catch { }
    $fqdn = [string]$cs.DNSHostName
    if ($cs.PartOfDomain) { $fqdn = ("{0}.{1}" -f $cs.DNSHostName, $cs.Domain) }
    return @{
        name            = [string]$env:COMPUTERNAME
        fqdn            = $fqdn
        domain          = [string]$cs.Domain
        part_of_domain  = [bool]$cs.PartOfDomain
        os              = ("{0} ({1})" -f $os.Caption, $os.Version)
        logical_cpus    = [int]$cs.NumberOfLogicalProcessors
        memory_total_mb = (To-MB $cs.TotalPhysicalMemory)
        memory_free_mb  = [int64][math]::Round([double]$os.FreePhysicalMemory / 1KB)
        vm_count        = @(Get-VM).Count
        vm_path         = [string]$vmhost.VirtualMachinePath
        switches        = $switches
        volumes         = $volumes
        addresses       = $addresses
    }
}

function Get-HvdClusterInfo {
    Import-Module FailoverClusters -ErrorAction Stop
    $c = Get-Cluster -ErrorAction Stop
    # Role of every cluster network (ClusterAndClient = 3 carries the management/client traffic)
    $netRoles = @{}
    try { foreach ($cn in @(Get-ClusterNetwork -ErrorAction Stop)) { $netRoles[[string]$cn.Name] = [string]$cn.Role } } catch { }
    $nodes = @()
    foreach ($n in @(Get-ClusterNode)) {
        $ips = @()
        try {
            foreach ($nic in @(Get-ClusterNetworkInterface -Node $n.Name -ErrorAction Stop)) {
                $role = [string]$netRoles[[string]$nic.Network]
                if (($role -eq 'ClusterAndClient' -or $role -eq '3') -and $nic.Address) { $ips += [string]$nic.Address }
            }
        } catch { }
        if ($ips.Count -eq 0) {
            try {
                foreach ($a in [System.Net.Dns]::GetHostAddresses([string]$n.Name)) {
                    if ($a.AddressFamily -eq 'InterNetwork') { $ips += $a.IPAddressToString }
                }
            } catch { }
        }
        $nodes += @{ name = [string]$n.Name; state = [string]$n.State; id = [string]$n.Id; addresses = $ips }
    }
    $csvs = @()
    foreach ($csv in @(Get-ClusterSharedVolume -ErrorAction SilentlyContinue)) {
        foreach ($vi in $csv.SharedVolumeInfo) {
            $csvs += @{
                name    = [string]$csv.Name
                path    = [string]$vi.FriendlyVolumeName
                owner   = [string]$csv.OwnerNode
                state   = [string]$csv.State
                size_gb = (To-GB $vi.Partition.Size)
                free_gb = (To-GB $vi.Partition.FreeSpace)
            }
        }
    }
    $cip = $null
    try {
        $res = Get-ClusterResource -ErrorAction Stop | Where-Object { [string]$_.ResourceType -eq 'IP Address' -and [string]$_.OwnerGroup -eq 'Cluster Group' } | Select-Object -First 1
        if ($res) { $cip = [string](($res | Get-ClusterParameter -Name Address).Value) }
    } catch { }
    $fqdn = [string]$c.Name
    if ($c.Domain) { $fqdn = ("{0}.{1}" -f $c.Name, $c.Domain) }
    return @{ name = [string]$c.Name; domain = [string]$c.Domain; fqdn = $fqdn; address = $cip; nodes = $nodes; csvs = $csvs }
}

function Set-HvdNicVlan($nic, $n) {
    # mode: access (default, uses $n.vlan; no VLAN = untagged) | trunk (allowed = "100,101,200-210")
    if ([string]$n.mode -eq 'trunk') {
        $allowed = [string]$n.allowed
        if (-not $allowed) { $allowed = '4094' }
        Set-VMNetworkAdapterVlan -VMNetworkAdapter $nic -Trunk -AllowedVlanIdList $allowed -NativeVlanId 0
    } elseif ($n.vlan) {
        Set-VMNetworkAdapterVlan -VMNetworkAdapter $nic -Access -VlanId ([int]$n.vlan)
    } else {
        Set-VMNetworkAdapterVlan -VMNetworkAdapter $nic -Untagged
    }
}

function Set-HvdNicSwitch($nic, $n) {
    # connected = $false keeps the adapter disconnected (no link in the guest)
    if ($n.switch -and ($n.connected -ne $false)) {
        if ([string]$nic.SwitchName -ne [string]$n.switch) { Connect-VMNetworkAdapter -VMNetworkAdapter $nic -SwitchName ([string]$n.switch) }
    } elseif ($nic.SwitchName) {
        Disconnect-VMNetworkAdapter -VMNetworkAdapter $nic
    }
}
