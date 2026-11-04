$exists = $false
try { $exists = [bool](Test-Path -LiteralPath ([string]$A.path) -ErrorAction Stop) } catch { $exists = $false }
return @{ path = [string]$A.path; exists = $exists; host = [string]$env:COMPUTERNAME }
