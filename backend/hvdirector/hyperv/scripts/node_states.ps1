Import-Module FailoverClusters -ErrorAction Stop
$nodes = @()
foreach ($n in @(Get-ClusterNode)) { $nodes += @{ name = [string]$n.Name; state = [string]$n.State } }
return @{ nodes = $nodes }
