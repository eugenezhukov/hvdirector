# Probe the address entered in the "Add cluster / host" wizard.
try { $null = Get-VMHost -ErrorAction Stop } catch { Fail 'NO_HYPERV' ("Hyper-V role/PowerShell module is not available on {0}: {1}" -f $env:COMPUTERNAME, $_.Exception.Message) }
$isCluster = $false
$svc = Get-Service -Name ClusSvc -ErrorAction SilentlyContinue
if ($svc -and [string]$svc.Status -eq 'Running') {
    try { Import-Module FailoverClusters -ErrorAction Stop; $null = Get-Cluster -ErrorAction Stop; $isCluster = $true } catch { $isCluster = $false }
}
$result = @{ is_cluster = $isCluster; host = (Get-HvdHostInfo) }
if ($isCluster) { $result.cluster = (Get-HvdClusterInfo) }
return $result
