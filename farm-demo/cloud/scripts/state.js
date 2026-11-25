var state;
if (metadata.farm_route === 'health') {
    state = {farm_health_ts: Number(metadata.farm_ts), farm_buffer_warning: metadata.farm_buffer === 'true'};
} else {
    state = {farm_state_seq: Number(metadata.farm_seq), farm_state_ts: Number(metadata.farm_ts),
             farm_status: Number(metadata.farm_status), expected_period_s: Number(metadata.farm_period)};
    if (metadata.farm_event === 'false') {
        state.farm_periodic_seq = Number(metadata.farm_seq);
        state.farm_periodic_ts = Number(metadata.farm_ts);
    }
}
return {msg: state, metadata: metadata, msgType: 'POST_ATTRIBUTES_REQUEST'};
