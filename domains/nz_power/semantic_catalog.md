# Semantic catalog

<!-- 自动生成：scripts/build_catalog.py（make catalog / make seed）。不要手改。 -->

Data coverage: 2026-04-01 ~ 2026-09-30 (daily grain).
Only the views and dimension tables listed below can be queried (schema main). Tables and columns not listed do not exist — do not guess.

## Time conventions
- Base month = the calendar month containing the base date, even if that month is not over yet.
- "Last N months / past N months" = N calendar months counting back, including the base month (base month 2026-09: last 3 months = 2026-07 ~ 2026-09, last 6 months = 2026-04 ~ 2026-09).
- "Last month" = the month before the base month; "past year / last 12 months" = the 12 calendar months including the base month.
- "This year so far / since <year>" = 1 January of that year ~ the end of the data coverage; quarters are calendar quarters (Q1 = Jan–Mar).

## Views

### main.mart_current_price
Columns: period_key (BIGINT), node_key (BIGINT), trading_date (DATE), trading_period (SMALLINT), period_start_local (TIMESTAMP), node_id (VARCHAR), price_nzd_per_mwh (DECIMAL(18,2)), price_version (VARCHAR)

### main.mart_daily_price
Columns: trading_date (DATE), node_key (BIGINT), node_id (VARCHAR), avg_price (DECIMAL(18,2)), min_price (DECIMAL(18,2)), max_price (DECIMAL(18,2)), period_count (BIGINT), interim_period_count (BIGINT)

### main.v_dim_node
Columns: node_key (BIGINT), node_id (VARCHAR), node_name (VARCHAR), island (VARCHAR), zone (VARCHAR), region (VARCHAR), first_seen_date (DATE)

### main.mart_daily_generation
Columns: trading_date (DATE), island (VARCHAR), zone (VARCHAR), fuel (VARCHAR), generation_kwh (HUGEINT), plant_count (BIGINT)

### main.mart_daily_generation_weighted_price
Columns: trading_date (DATE), island (VARCHAR), fuel (VARCHAR), generation_kwh (HUGEINT), interim_generation_kwh (HUGEINT), weighted_avg_price (DECIMAL(18,2))

## Dimension tables

## Dimension values
When filtering by name, use only these exact values (character for character), or the corresponding ID; do not abbreviate and do not use LIKE.

### Node (node_id · node_name · island · zone · first_seen_date)
- ABY0111 · ALBURY · SI · LSI
- ALB0331 · ALBANY · NI · UNI
- ALB1101 · ALBANY · NI · UNI
- APS0111 · ARTHURS PASS · SI · USI
- ARA2201 · ARATIATIA · NI · CNI
- ARG1101 · ARGYLE · SI · USI
- ARI1101 · ARAPUNI · NI · CNI
- ARI1102 · Arapuni · NI · CNI
- ASB0661 · ASHBURTON · SI · USI
- ASY0111 · ASHLEY · SI · USI
- ATI2201 · ATIAMURI · NI · CNI
- AVI2201 · AVIEMORE · SI · LSI
- BAL0331 · BALCLUTHA · SI · LSI
- BDE0111 · BRYDONE · SI · LSI
- BEN2201 · BENMORE · SI · LSI
- BEN2202 · BENMORE · SI · LSI
- BLN0331 · BLENHEIM · SI · USI
- BOB1101 · BOMBAY · NI · UNI
- BPD1101 · Bells Pond · SI · LSI
- BPE0331 · BUNNYTHORPE · NI · LNI
- BPE0551 · BUNNYTHORPE · NI · LNI
- BPE2201 · BUNNYTHORPE · NI · LNI
- BPT1101 · BLACKPOINT · SI · LSI
- BRB0331 · BREAM BAY · NI · UNI
- BRK0331 · BRUNSWICK · NI · LNI
- BRY0661 · BROMLEY · SI · USI
- BWK1101 · BERWICK · SI · LSI
- CBG0111 · CAMBRIDGE · NI · CNI
- CLH0111 · CASTLE HILL · SI · USI
- CML0331 · CROMWELL · SI · LSI
- COL0111 · COLERIDGE · SI · USI
- COL0661 · COLERIDGE · SI · USI
- CPK0111 · Central Park · NI · LNI
- CPK0331 · Central Park · NI · LNI
- CST0331 · CARRINGTON ST · NI · LNI
- CUL0331 · CULVERDEN · SI · USI
- CUL0661 · Culverden · SI · USI
- CYD0331 · CLYDE · SI · LSI
- CYD2201 · CLYDE · SI · LSI
- DOB0331 · DOBSON · SI · USI
- DOB0661 · DOBSON · SI · USI
- DVK0111 · Dannevirke Solar Farm · NI · LNI
- EDG0331 · EDGECUMBE · NI · CNI
- EDN0331 · EDENDALE · SI · LSI
- FHL0331 · FERNHILL · NI · CNI
- FKN0331 · Frankton · SI · LSI
- GFD0331 · Gracefield · NI · LNI
- GLN0331 · GLENBROOK · NI · UNI
- GLN0332 · GLENBROOK · NI · UNI
- GOR0331 · GORE · SI · LSI
- GYM0661 · GREYMOUTH · SI · USI
- GYT0331 · GREYTOWN · NI · LNI
- HAM0111 · HAMILTON · NI · CNI
- HAM0331 · HAMILTON · NI · CNI
- HAM0551 · HAMILTON · NI · CNI
- HAM2201 · HAMILTON · NI · CNI
- HAY0111 · Haywards · NI · LNI
- HAY0331 · Haywards · NI · LNI
- HAY1101 · Haywards · NI · LNI
- HAY2201 · Haywards · NI · LNI
- HEN0331 · HENDERSON · NI · UNI
- HEN2201 · HENDERSON · NI · UNI
- HEP0331 · HEPBURN ROAD · NI · UNI
- HIN0331 · HINUERA · NI · CNI
- HKK0661 · HOKITIKA · SI · USI
- HLY0331 · Huntly · NI · CNI
- HLY2201 · HUNTLY · NI · CNI
- HOB1101 · HOBSON STREET · NI · UNI
- HOR0331 · HORORATA · SI · USI
- HOR0661 · HORORATA · SI · USI
- HRP2201 · Harapaki · NI · CNI
- HTI0331 · HANGATIKI · NI · CNI
- HTI1101 · Hangatiki · NI · CNI
- HTU0331 · Forrest Road Cambridge · NI · CNI
- HUI0331 · HUIRANGI · NI · LNI
- HWA0331 · HAWERA · NI · LNI
- HWA0332 · KUPE GAS FIELD · NI · LNI
- HWA1101 · HAWERA · NI · LNI
- HWA1102 · HAWERA · NI · LNI
- HWB0331 · HALFWAY BUSH · SI · LSI
- HWB1101 · HALF WAY BUSH · SI · LSI
- HWB2201 · HALFWAY BUSH · SI · LSI
- INV0331 · INVERCARGILL · SI · LSI
- INV0661 · INVERCARGILL · SI · LSI
- INV2201 · INVERCARGILL · SI · LSI
- INV2202 · INVERCARGILL · SI · LSI
- ISL0331 · ISLINGTON · SI · USI
- ISL0661 · ISLINGTON · SI · USI
- ISL2201 · ISLINGTON · SI · USI
- JRD1101 · Junction Road · NI · LNI
- KAI0111 · KAIAPOI · SI · USI
- KAW0111 · KAWERAU · NI · CNI
- KAW0112 · Kawerau · NI · CNI
- KAW1101 · KAWERAU GEOTHERMAL · NI · CNI
- KAW2201 · TOPP2 · NI
- KBY0661 · KIMBERLEY · SI · USI
- KBY0662 · KIMBERLEY · SI · USI
- KIK0111 · KIKIWA · SI · USI
- KIK2201 · KIKIWA · SI · USI
- KIN0111 · KINLEITH · NI · CNI
- KIN0112 · KINLEITH · NI · CNI
- KIN0113 · KINLEITH · NI · CNI
- KIN0331 · KINLEITH · NI · CNI
- KIW2201 · Kaiwera Downs · SI · LSI
- KMO0331 · Kaitemako · NI · CNI
- KOE1101 · Kaikohe · NI · UNI
- KPA1101 · KAPONGA · NI · LNI
- KPO1101 · KARAPIRO · NI · CNI
- KPU0661 · KOPU · NI · CNI
- KUM0661 · KUMARA · SI · USI
- KWA0111 · Kaiwharawhara · NI · LNI
- LFD1101 · LICHFIELD · NI · CNI
- LFD1102 · LICHFIELD · NI · CNI
- LTN0331 · LINTON · NI · LNI
- LTN2201 · TURITEA · NI · LNI
- MAN2201 · MANAPOURI · SI · LSI
- MAT1101 · MATAHINA · NI · CNI
- MCH0111 · MURCHISON · SI · USI
- MDN1101 · MARSDEN · NI · UNI
- MDN2201 · MARSDEN · NI · UNI
- MGM0331 · MANGAMAIRE · NI · LNI
- MHO0331 · MANGAHAO · NI · LNI
- MKE1101 · McKee · NI · LNI
- MLG0111 · Melling · NI · LNI
- MLG0331 · Melling · NI · LNI
- MNG0331 · MANGERE · NI · UNI
- MNG1101 · MANGERE · NI · UNI
- MNI0111 · OMV NZ Production Ltd · NI · LNI
- MPE1101 · MAUNGATAPERE · NI · UNI
- MST0331 · MASTERTON · NI · LNI
- MTI2201 · MARAETAI · NI · CNI
- MTM0331 · MT. MAUNGANUI · NI · CNI
- MTN0331 · MARTON · NI · LNI
- MTO0331 · MAUNGATUROTO · NI · UNI
- MTR0331 · MATAROA · NI · LNI
- NAP2201 · Nga Awa Purua · NI · CNI
- NAP2202 · NGATAMARIKI · NI · CNI
- NMA0331 · NTH MAKAREWA · SI · LSI
- NPK0331 · NATIONAL PARK · NI · CNI
- NSY0331 · NASEBY · SI · LSI
- NWD0661 · Norwood · SI · USI
- OAM0331 · OAMARU · SI · LSI
- OHA2201 · OHAU A · SI · LSI
- OHB2201 · OHAU B · SI · LSI
- OHC2201 · OHAU C · SI · LSI
- OHG1101 · OHANGAI · NI · LNI · 2026-06-24
- OHK2201 · OHAKURI · NI · CNI
- OKI2201 · OHAAKI · NI · CNI
- OKN0111 · OHAKUNE · NI · CNI
- ONG0331 · ONGARUE · NI · CNI
- OPK0331 · OPUNAKE · NI · LNI
- ORO1101 · OROWAITI · SI · USI
- ORO1102 · OROWAITI · SI · USI
- OTA0221 · OTAHUHU · NI · UNI
- OTA2201 · OTAHUHU · NI · UNI
- OTI0111 · OTIRA · SI · USI
- OWH0111 · OWHATA · NI · CNI
- PAK0331 · PAKURANGA · NI · UNI
- PAO1101 · PIAKO 110KV · NI · CNI
- PEN0221 · PENROSE · NI · UNI
- PEN0251 · Penrose · NI · UNI
- PEN0331 · PENROSE · NI · UNI
- PEN1101 · PENROSE · NI · UNI
- PNI0331 · Pauatahanui · NI · LNI
- PPI2201 · POIHIPI · NI · CNI
- PRM0331 · PARAPARAUMU · NI · LNI
- RDF0331 · REDCLYFFE · NI · CNI
- RDF2201 · REDCLYFFE · NI · CNI
- RFN1101 · REEFTON · SI · USI
- RFN1102 · REEFTON · SI · USI
- ROS0221 · MT. ROSKILL · NI · UNI
- ROS1101 · MT. ROSKILL · NI · UNI
- ROT0111 · ROTORUA · NI · CNI
- ROT0331 · ROTORUA · NI · CNI
- ROT1101 · ROTORUA · NI · CNI
- ROX1101 · ROXBURGH · SI · LSI
- ROX2201 · ROXBURGH · SI · LSI
- RPO2201 · RANGIPO · NI · CNI
- SBK0661 · SOUTHBROOK · SI · USI
- SDN0331 · SOUTH DUNEDIN · SI · LSI
- SFD0331 · STRATFORD · NI · LNI
- SFD2201 · STRATFORD · NI · LNI
- STK0331 · STOKE · SI · USI
- STK0661 · STOKE · SI · USI
- STK2201 · STOKE · SI · USI
- STU0111 · STUDHOLME · SI · LSI
- SVL0331 · SILVERDALE · NI · UNI
- SWN0251 · NZ Rail Southdown · NI · UNI
- SWN2201 · SOUTHDOWN · NI · UNI
- TAB0331 · Tauhara · NI · CNI
- TAB2201 · TAUHARA · NI · CNI
- TAK0331 · TAKANINI · NI · UNI
- TGA0111 · TAURANGA · NI · CNI
- TGA0331 · TAURANGA · NI · CNI
- THI2201 · TE MIHI · NI · CNI
- TIM0111 · TIMARU · SI · LSI
- TKA0111 · Tekapo A · SI · LSI
- TKA0331 · TEKAPO A · SI · LSI
- TKB2201 · Tekapo B · SI · LSI
- TKR0331 · Takapu Road · NI · LNI
- TKU0331 · TOKAANU · NI · CNI
- TKU2201 · TOKAANU · NI · CNI
- TMI0331 · TE MATAI · NI · CNI
- TMK0331 · TEMUKA · SI · LSI
- TMN0551 · TAUMARUNUI · NI · CNI
- TMU0111 · TE AWAMUTU · NI · CNI
- TMU1101 · TE AWAMUTU · NI · CNI
- TNG0551 · TANGIWAI · NI · CNI
- TRK0111 · TARUKENGA · NI · CNI
- TRK2201 · TARUKENGA · NI · CNI
- TUI1101 · TUIA FOR EAST · NI · CNI
- TWC2201 · NZ Windfarms · NI · LNI
- TWH0331 · TE KOWHAI · NI · CNI
- TWI2201 · TIWAI POINT · SI · LSI
- TWZ0331 · TWIZEL · SI · LSI
- UHT0331 · Upper Hutt · NI · LNI
- WAI0111 · WAIOTAHI · NI · CNI
- WAI0331 · WAIOTAHI · NI · CNI
- WAI0501 · WAIOTAHI · NI · CNI
- WDV0111 · WOODVILLE · NI · LNI
- WDV1101 · WOODVILLE · NI · LNI
- WEL0331 · WELLSFORD · NI · UNI
- WGN0331 · WANGANUI · NI · LNI
- WHI0111 · WHIRINAKI · NI · CNI
- WHI2201 · WHIRINAKI · NI · CNI
- WHU0331 · WAIHOU · NI · CNI
- WHU1101 · Tauhei Solar Farm · NI · CNI · 2026-04-08
- WIL0331 · Wilton · NI · LNI
- WIR0331 · WIRI · NI · UNI
- WKM2201 · WHAKAMARU · NI · CNI
- WKO0331 · WAIKINO · NI · CNI
- WPA2201 · WAIPAPA · NI · CNI
- WPR0331 · WAIPARA · SI · USI
- WPR0661 · WAIPARA · SI · USI
- WPW0331 · WAIPAWA · NI · LNI
- WRD0331 · WAIRAU ROAD · NI · UNI
- WRK0331 · WAIRAKEI · NI · CNI
- WRK2201 · WAIRAKEI · NI · CNI
- WTK0111 · WAITAKI · SI · LSI
- WTK0331 · WAITAKI · SI · LSI
- WTK2201 · WAITAKI · SI · LSI
- WTU0331 · WHAKATU · NI · CNI
- WVY0111 · WAVERLEY · NI · LNI
- WVY1101 · WAVERLEY · NI · LNI
- WWD1102 · Westwind · NI · LNI
- WWD1103 · Westwind · NI · LNI

### Region (region; NULL for the 18 transmission-grid nodes)
- Ashburton (Electricity Ashburton)
- Auckland (Vector)
- Bay of Islands (Top Energy)
- Buller (Buller Electricity)
- Central Canterbury (Orion New Zealand)
- Central Hawke's Bay (Centralines)
- Central Otago (Aurora Energy)
- Counties (Counties Power)
- Dunedin (Aurora Energy)
- Eastern Bay of Plenty (Horizon Energy)
- Frankton (Lakelands)
- Hawke's Bay (Unison Networks)
- Invercargill (Electricity Invercargill)
- King Country (The Lines Company)
- Kāpiti and Horowhenua (Electra)
- Manawatū (Powerco)
- Marlborough (Marlborough Lines)
- North Canterbury (MainPower NZ)
- Otago (OtagoNet)
- Rotorua (Unison Networks)
- South Canterbury (Alpine Energy)
- Southern Hawke's Bay (Scanpower)
- Southland (The Power Company)
- Tairāwhiti and Wairoa (Firstlight Network)
- Taranaki (Powerco)
- Tasman (Network Tasman)
- Taupō (Unison Networks)
- Tauranga (Powerco)
- Thames Valley (Powerco)
- Waikato (WEL Networks)
- Waipa (Waipa Networks)
- Wairarapa (Powerco)
- Waitaki (Network Waitaki)
- Waitemata (Vector)
- Wellington (Wellington Electricity)
- West Coast (Westpower)
- Whanganui (Powerco)
- Whangārei and Kaipara (Northpower)

### Fuel (fuel)
- Battery storage
- Coal
- Diesel
- Gas
- Geothermal
- Hydro
- Solar
- Wind
- Wood
