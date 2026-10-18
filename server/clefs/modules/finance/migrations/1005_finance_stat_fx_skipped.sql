-- Finance: the channel rows a month's statistical inputs left out because no
-- exchange rate reached the hotel's currency (review.compute_stat_inputs).
-- WHY stored: PMS rooms revenue and expected OTA commission are summed in
-- the hotel's currency; a USD or EUR row with no rate is dropped rather
-- than added raw, and check 2 has to say so when it reads the stored row.
-- A list of {channel_code, currency, room_rev_minor, commission_minor}.

ALTER TABLE fin_stat_inputs ADD COLUMN IF NOT EXISTS fx_skipped JSONB;
