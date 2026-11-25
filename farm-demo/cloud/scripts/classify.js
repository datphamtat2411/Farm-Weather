// Routing and watermarks are evaluated on the Cloud, not on measurement averages.
var gateway = __GATEWAY__;
function integer(v) { return typeof v === 'number' && isFinite(v) && Math.floor(v) === v; }
function invalid(reason) {
    metadata.farm_route = 'invalid'; metadata.farm_reason = reason;
    return {msg: {}, metadata: metadata, msgType: msgType};
}
var ts = Number(metadata.ts);
var now = Date.now();
if (!integer(ts) || ts <= 0) return invalid('timestamp');
var period = Number(metadata.ss_expected_period_s || 300);
if (period !== 300 && period !== 900) period = 300;
metadata.farm_ts = String(ts);
metadata.farm_save = 'latest';
metadata.farm_status = String(metadata.ss_farm_status || 0);
if (gateway) {
    if (msg.farm_health !== true || !integer(msg.disk_bytes) || !integer(msg.budget_bytes) ||
        msg.disk_bytes < 0 || msg.budget_bytes <= 0 || !integer(msg.pending) || msg.pending < 0 ||
        ['ONLINE','OFFLINE','RECOVERING'].indexOf(msg.link_state) < 0) return invalid('gateway health');
    if (ts <= Number(metadata.ss_farm_health_ts || 0) || ts > now+300000) return invalid('old health');
    var prior = String(metadata.ss_farm_buffer_warning || 'false') === 'true';
    metadata.farm_buffer = String(msg.disk_bytes*100 >= msg.budget_bytes*(prior ? 70 : 80));
    metadata.farm_route = 'health';
} else if (msg.farm_watchdog === true) {
    var last = Number(metadata.ss_farm_state_ts || metadata.ss_farm_monitor_since || now);
    metadata.farm_route = 'tick';
    metadata.farm_stale = String(now-last > period*2000);
} else {
    var event = typeof msg.event_record_id === 'string';
    var seq = event ? msg.event_sequence_number : msg.sequence_number;
    var id = event ? msg.event_record_id : msg.record_id;
    var status = event ? msg.event_status : msg.status;
    var p = event ? msg.event_requested_period_s : msg.period_s;
    if (!integer(seq) || seq <= 0 || seq > 4294967295 ||
        id !== metadata.deviceName+':'+seq || !integer(status) || status < 0 || status > 31 ||
        (p !== 300 && p !== 900) ||
        (msg.sync_mode !== 'live' && msg.sync_mode !== 'history')) return invalid('weather identity/status');
    if (!event) {
        if (!integer(msg.valid_mask) || msg.valid_mask < 0 || msg.valid_mask > 127) return invalid('quality');
        var groups = [['temperature'],['humidity'],['pressure'],['wind_speed','wind_gust'],
                      ['wind_direction'],['rainfall'],['battery_voltage']];
        for (var i=0; i<groups.length; i++) {
            for (var j=0; j<groups[i].length; j++) {
                var k=groups[i][j];
                if ((msg.valid_mask & (1<<i)) === 0) {
                    if (Object.prototype.hasOwnProperty.call(msg,k)) return invalid('invalid field present');
                } else if (typeof msg[k] !== 'number' || !isFinite(msg[k])) return invalid('missing valid field');
            }
        }
    }
    var fresh = msg.sync_mode === 'live' && ts <= now && now-ts <= p*2000;
    delete msg.sync_mode;
    var newer = seq > Number(metadata.ss_farm_state_seq || 0) && ts >= Number(metadata.ss_farm_state_ts || 0);
    var latest = fresh && (event ? newer :
        seq > Number(metadata.ss_farm_periodic_seq || 0) && ts >= Number(metadata.ss_farm_periodic_ts || 0));
    metadata.farm_save = latest ? 'latest' : 'history';
    metadata.farm_route = fresh && newer ? 'state' : metadata.farm_save;
    metadata.farm_record_id = id;
    metadata.farm_seq = String(seq);
    metadata.farm_status = String(status);
    metadata.farm_period = String(p);
    metadata.farm_event = String(event);
    // A periodic sample older than a same-second event may update measurement
    // latest, but never the state. Store the periodic cursor only on state path.
    if (metadata.farm_route === 'latest') metadata.farm_route = 'history';
}
metadata.farm_original = JSON.stringify(msg);
return {msg: msg, metadata: metadata, msgType: msgType};
