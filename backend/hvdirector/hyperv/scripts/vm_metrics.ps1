# $A: ids: [VM id, ...] -> raw cumulative load counters of those VMs that run on this host.
#   CPU:     "Hyper-V Hypervisor Virtual Processor" raw counters (% Total Run Time, 100 ns units) per VM,
#            CIM class names are not localized (works on Russian Windows Server as well).
#   Memory:  assigned / demand from Get-VM.
#   Disk, network: Hyper-V resource metering (Measure-VM, cumulative MB; enabled on first use).
$want = @{}
foreach ($i in $A.ids) { $want[([string]$i).ToUpper()] = $true }
$errors = @()
$vms = @(Get-VM | Where-Object { $want.ContainsKey(([string]$_.Id).ToUpper()) })
$vp = @{}
if ($vms.Count -gt 0) {
    try {
        foreach ($c in @(Get-CimInstance -ClassName Win32_PerfRawData_HvStats_HyperVHypervisorVirtualProcessor -ErrorAction Stop)) {
            $name = [string]$c.Name
            $i = $name.LastIndexOf(':')
            if ($i -lt 1) { continue }
            $key = $name.Substring(0, $i)
            if (-not $vp.ContainsKey($key)) { $vp[$key] = @{ run = [uint64]0; n = 0; ts = [uint64]0 } }
            $vp[$key].run += [uint64]$c.PercentTotalRunTime
            $vp[$key].n += 1
            $vp[$key].ts = [uint64]$c.Timestamp_Sys100NS
        }
    } catch { $errors += ('cpu counters: ' + $_.Exception.Message) }
}
$out = @()
foreach ($vm in $vms) {
    $name = [string]$vm.Name
    $item = @{
        id = [string]$vm.Id; name = $name; state = [string]$vm.State; vcpu = [int]$vm.ProcessorCount
        cpu_usage = [int]$vm.CPUUsage; mem_assigned_mb = (To-MB $vm.MemoryAssigned); mem_demand_mb = (To-MB $vm.MemoryDemand)
        cpu = $null; disk = $null; net = $null
    }
    if ($vp.ContainsKey($name)) { $item.cpu = $vp[$name] }
    if ([string]$vm.State -eq 'Running') {
        try {
            if (-not $vm.ResourceMeteringEnabled) { Enable-VMResourceMetering -VM $vm -ErrorAction Stop }
            $m = Measure-VM -VM $vm -ErrorAction Stop
            $rx = [uint64]0; $tx = [uint64]0; $acl = 0
            foreach ($r in @($m.NetworkMeteredTrafficReport)) {
                if (-not $r) { continue }
                $acl++
                if ([string]$r.Direction -eq 'Inbound') { $rx += [uint64]$r.TotalTraffic } else { $tx += [uint64]$r.TotalTraffic }
            }
            if ($acl -eq 0) {
                # adapters without metering ACLs (e.g. added after metering was enabled)
                foreach ($a in @(Get-VMNetworkAdapter -VM $vm)) {
                    Add-VMNetworkAdapterAcl -VMNetworkAdapter $a -RemoteIPAddress '0.0.0.0/0' -Direction Both -Action Meter -ErrorAction SilentlyContinue
                    Add-VMNetworkAdapterAcl -VMNetworkAdapter $a -RemoteIPAddress '::/0' -Direction Both -Action Meter -ErrorAction SilentlyContinue
                }
            } else {
                $item.net = @{ rx_mb = $rx; tx_mb = $tx }
            }
            $item.disk = @{ read_mb = [uint64]$m.AggregatedDiskDataRead; write_mb = [uint64]$m.AggregatedDiskDataWritten
                            io = [uint64]$m.AggregatedNormalizedIOCount }
        } catch { $errors += ('metering ' + $name + ': ' + $_.Exception.Message) }
    }
    $out += $item
}
return @{ host = $env:COMPUTERNAME; logical_cpus = [int](Get-VMHost).LogicalProcessorCount; vms = $out; errors = $errors }
