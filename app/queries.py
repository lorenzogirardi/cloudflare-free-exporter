"""GraphQL queries, restricted to what a Cloudflare FREE zone actually serves.

Verified against a Free Website zone on 2026-08-08. Do not add these fields back:
originResponseDurationMs, edgeTimeToFirstByteMs, clientAsn, clientASNDescription,
botScore*, botManagementDecision — the API answers `code: authz` for all of them,
and one refused field fails the whole query. Same for the datasets
httpRequests1mGroups and firewallEventsAdaptiveGroups (disabled on Free).
"""

OVERVIEW_MINUTE = """query($z:string!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
 httpRequestsOverviewAdaptiveGroups(limit:2000,filter:{datetime_geq:$s,datetime_lt:$u},
  orderBy:[datetimeMinute_ASC]){
  dimensions{datetimeMinute edgeResponseStatus}
  sum{requests bytes cachedRequests cachedBytes visits pageViews}}}}}"""

OVERVIEW_GEO = """query($z:string!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
 httpRequestsOverviewAdaptiveGroups(limit:500,filter:{datetime_geq:$s,datetime_lt:$u},orderBy:[sum_requests_DESC]){
  dimensions{clientCountryName clientRequestHTTPProtocol} sum{requests bytes}}}}}"""

HOSTS = """query($z:string!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
 httpRequestsAdaptiveGroups(limit:500,filter:{datetime_geq:$s,datetime_lt:$u},orderBy:[count_DESC]){
  count dimensions{clientRequestHTTPHost edgeResponseStatus cacheStatus} sum{edgeResponseBytes}}}}}"""

DNS = """query($z:string!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
 dnsAnalyticsAdaptiveGroups(limit:500,filter:{datetime_geq:$s,datetime_lt:$u},orderBy:[count_DESC]){
  count dimensions{queryName queryType responseCode responseCached coloName} avg{processingTimeUs}}}}}"""

FIREWALL = """query($z:string!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
 firewallEventsAdaptive(limit:1000,filter:{datetime_geq:$s,datetime_lt:$u},orderBy:[datetime_DESC]){
  action source clientCountryName clientRequestHTTPHost}}}}"""

DAILY = """query($z:string!,$d:string!){viewer{zones(filter:{zoneTag:$z}){
 httpRequests1dGroups(limit:2,filter:{date_geq:$d},orderBy:[date_DESC]){
  dimensions{date} sum{requests bytes threats} uniq{uniques}}}}}"""
