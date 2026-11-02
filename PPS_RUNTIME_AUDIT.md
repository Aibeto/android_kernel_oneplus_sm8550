# OPLUS v1 PPS runtime audit — OnePlus 11 / 22811 (SM8550, salami, 2S)

Branch `feat/16.0/oneplus_11/pps`. Every claim below cites `file:line`. All source under
`devicetree/sm8550/vendor/oplus/kernel/charger/` unless prefixed otherwise.
Goal: **what must be true (DT + module code) for PPS to actually run and deliver 11 V / 5 A.**

---

## 0. Verdicts up front

| # | Question | Verdict |
|---|---|---|
| 1 | `pps_support_type` | Non-zero is mandatory (`oplus_pps.c:499-503`). Only **1/2/3/4** are meaningful (enum `PPS_SUPPORT_MCU/2CP/3CP/VOOCPHY`, `oplus_pps.h:274-279`). `1` is **not** the right value here (it derives VBAT from an ADSP op we cannot rely on) — see §1. |
| 2 | DT must-have | 7 properties are hard requirements; everything else has defaults. Table in §2. |
| 3 | Curves | First curve `target_vbus` is **not** the initial ask — it is only the *ceiling*. Initial ask is computed from VBAT. §3. |
| 4 | Ops with no CP | `cp-sc8571` ops **are** registered unconditionally, so `chip->ops != NULL`, but every CP-domain getter returns **0**, and `data.ap_input_current` is *only* filled from those getters (`oplus_pps.c:1505`). **This is the blocking defect**: the third-party control loop has no current feedback. §4. |
| 5 | Write path | `oplus_chg_set_pps_config()` → 2 ADSP SETs, no clamp; `rc==0` = "ADSP acked the message", **not** "PPS applied". §5. |
| 6 | Region gate | Hard gate. `dbg_nvid` **cannot** substitute. §6. |
| 7 | 55 W | Hard clamp `PPS_3RD_ASK_VOLT_MAX = 10500` (`oplus_pps.h:141`, used `oplus_pps.c:4212-4213`). §7. |
| 8 | Code changes | 3 mandatory + 2 recommended, §8. |
| 9 | DT node | §9, transcribable. |

**Bottom line:** stock-module-only operation is *not* plausible. DT alone gets the engine to
start, but it will either (a) ramp VBUS to the clamp and then trip `PPS_STOP_VOTER_DISCONNECT_OVER`
because `ap_input_current == 0`, or (b) stop with `adjust volt fail`. A ~40-line ops/LUT change is
required (§8). Also verify `oplus_region=` in bootargs before anything else (§6) — if it is missing,
even a perfect DT will keep third-party PPS off.

---

## 1. `pps_support_type` semantics

Enum (`oplus_pps.h:274-279`): `PPS_SUPPORT_NOT=0, PPS_SUPPORT_MCU=1, PPS_SUPPORT_2CP=2, PPS_SUPPORT_3CP=3, PPS_SUPPORT_VOOCPHY=4`.

| Value | Meaning | Runtime consequences |
|---|---|---|
| 0 / absent | engine disabled | `oplus_pps.c:499-503` returns `-ENODEV`, `pps_support_type=0`; every entry point short-circuits; `oplus_pps_get_chg_status()` → `PPS_NOT_SUPPORT` (`:5114`). |
| 1 MCU | "PPS done by an MCU in the cable/adapter" | `vbat = data.vbat0 / 2` (`:3908-3911`, `:4176-4179`, `:4921-4924`) and `data.ap_input_current` is hard-set to **0** (`:1485`). All CP fields forced 0. |
| 2 2CP | one CP | slave-CP enable/disable checks run (`:3642`, `:3739`), `show_power` clamps to `OPLUS_PPS_POWER_V2` (`:5514-5517`). |
| 3 3CP | two/three CP | adds `slave_b` handling (`:1308`, `:1790`, `:1820`), `pps_strategy_normal_bypass_limit_current` becomes active (`:2231-2233`), `show_power` → `V3`. |
| 4 VOOCPHY | voocphy-owned PPS | `oplus_support_pps()` returns `chip->pps_support_third` (`:5161-5166`); otherwise treated like a non-MCU provider. |

**Is `<1>` enough?** It satisfies the gate, but it is the *worst* choice for 22811:
- `vbat = data.vbat0 / 2` where `data.vbat0 = ops->get_vbat0_volt()` = `oplus_sm8350_read_vbat0_voltage()`
  → ADSP property `USB_PPS_READ_VBAT0_VOLT` (`charger_ic/oplus_battery_sm8550.c:11503-11525`).
  If that property is unimplemented the return is `-1` → `oplus_pps_get_vbat0()` clamps `<0` to `0`
  (`oplus_pps.c:3495-3510`) → start target `= 0 + 300 mV`, i.e. the engine asks ~300 mV.
- MCU also forces `ap_input_current = 0` (`:1485`), guaranteeing the §4 feedback defect.

Recommendation: use **`<2>`** (2CP) with a real *or* shimmed ops provider — 2CP keeps
`ap_input_current` on the `cp_master+slave+slave_b` path (`:1505-1507`) and does not enable the
`3CP`-only bypass-current clamp. There is **no in-tree precedent** for any value: a grep of all three
DT trees (`devicetree/sm8550|sm8650|sm8750`) finds `pps_support_type` only in the uncommitted draft.

---

## 2. Definitive property set on `&battery_charger` (v1 runtime path)

`chip->dev->of_node` is the `battery_charger` platform device (compatible `"qcom,battery-charger"`),
set in `oplus_pps_init()` (`oplus_pps.c:5384`) called from `oplus_chg_init()` (`oplus_charger.c:4597`).

### 2.1 Hard requirements (absence breaks the run)

| Property | Type | Absent ⇒ |
|---|---|---|
| `oplus,pps_support_type` | u32 | **engine off** (`oplus_pps.c:499-503`) |
| `oplus,pps_support_third` | u32 | `pps_support_third=0` (`:508-510`) **and** `third_pps_supported_from_nvid()` must be true or it is forced back to 0 (`:512`) |
| `oplus,pps_region_list` | u32-array | third-party permanently denied, see §6 (`v1/oplus_region_check.c:164-178`) |
| `oplus,pps_ops` | **string** | falls back to `"mcu-op10"` (`oplus_pps_ops_manager.c:71-74`); that provider only registers from `vooc_ic/oplus_op10.c:1151` probe (compatible `oplus,op10`, absent on 22811) ⇒ `oplus_pps_ops_get()` → NULL ⇒ `chip->ops == NULL` (`oplus_pps.c:5394`), 100× retry then nothing (`:4881-4891`) |
| `pps_charge_third_strategy` | child node + 6×6 props | `oplus_pps.c:915-921` → `-EINVAL`; `batt_curves_third_*` all zero ⇒ `batt_curve_num == 0` ⇒ `batt_curve_index = -1` (`:1667-1671`) and OOB-ish reads through `oplus_pps_get_curve_vbus()` (`:1171`) |
| `oplus,pps_charge_strategy_soc` | u32×7 | default array `:490`, count default 7 (`:785-798`) — the *child-node names* are fixed by `pps_strategy_soc[]` (`:212-219`), so the 7 elements only have to agree with the 6 children you provide |
| `oplus,pps_charge_strategy_temp` | u32×7 | default array `:491`, count default 7 (`:755-768`); names fixed by `pps_strategy_temp[]` (`:221-228`) |

### 2.2 Everything else parsed (with its compiled-in default)

All in `oplus_pps_parse_charge_strategy()` (`oplus_pps.c:486-836`) and
`oplus_pps_parse_resistense_strategy()` (`:838-901`). “Absent is safe” = default is sane for 22811.

| Property | Default | Used at runtime by | Absent safe? |
|---|---|---|---|
| `oplus,pps_warm_allow_vol` | 4000 (`:518`) | `oplus_pps_is_allow_real()` (`:5471`) | yes |
| `oplus,pps_warm_allow_soc` | 50 (`:524`) | same (`:5471`) | yes |
| `oplus,pps_strategy_common_chg_current` | `-EINVAL` (`:532`) | only if `third_pps_supported_comm_chg_nvid()` (`:543-547`) | yes |
| `oplus,pps_strategy_normal_current` | **6000** (`:539`) | **the IBUS setpoint for third-party**: `:4228`, `:4245`, and the loop target in `oplus_3rd_pps_action_volt_change`/`_status_check` (`:4159`, `:4293`) | yes, but set it explicitly |
| `oplus,pps_strategy_normal_bypass_limit_current` | 12000 (`:554`) | only when `support_type == 3CP` (`:2231-2233`) | yes |
| `oplus,pps_over_high_or_low_current` | `-EINVAL` (`:563`) | over/under-temp current (`:1861`, `:1957`, `:2129`, `:2172`) | **no** — `-EINVAL` as a current → negative target; set it |
| `oplus,pps_full_cool_sw_vbat` | 4430 (`:569`) | full detect (`:3005`) | yes |
| `oplus,pps_full_normal_sw_vbat` | 4495 (`:576`) | full detect (`:3006`), curve check (`:2320`) | yes |
| `oplus,pps_full_normal_hw_vbat` | 4500 (`:583`) | full detect (`:3007`) | yes |
| `oplus,pps_full_cool_sw_vbat_third` | 4430 (`:590`) | third full detect (`:3000`) | yes |
| `oplus,pps_full_normal_sw_vbat_third` | 4430 (`:598`) | third full detect (`:3002`) | yes |
| `oplus,pps_full_normal_hw_vbat_third` | 4440 (`:606`) | third full detect (`:3003`) | yes |
| `oplus,pps_full_ffc_vbat` | 4420 (`:613`) | FFC (`:3025`) | yes |
| `oplus,pps_full_warm_vbat` | 4150 (`:620`) | warm full (`:3046`) | yes |
| `oplus,pps_timeout_third` | 9000 (`:627`) | **seconds** (`:2794-2796`, tick = `UPDATE_FASTCHG_TIME 1 s`, `oplus_pps.h:43`) → `PPS_STOP_VOTER_TIME_OVER` (`:3010`) | yes |
| `oplus,pps_timeout_oplus` | 7200 (`:634`) | same, non-third | yes |
| `oplus,pps_ibat_over_third` | 4000 (`:641`) | `pps_ibat_high` stop (`:3150-3161`) | **no** — 4000 would stop 55 W instantly |
| `oplus,pps_ibat_over_oplus` | 17000 (`:648`) | same, non-third | yes |
| `oplus,pps_exit_ms` | 1 (`:654`) | exit delay (`oplus_pps_pd_exit` path) | yes |
| `oplus,pps_flash_unsupport` | bool, unset | flash-led interaction (`:4562`) | yes |
| `oplus,pps_bcc_max_curr/min/exit` | 15000/1000/1000 (`:662`, `:669`, `:676`) | BCC window; recomputed from the curve anyway (`:1560-1567`) | yes |
| `oplus,pps_switch_soc_v2/power_v2/soc_v3/power_v3` | 95/30/75/100 (`:683`, `:690`, `:697`, `:704`) | power-class switch, non-third | yes |
| `oplus,pps_strategy_batt_high_temp` | `{425,430,435,400,415,420}` (`:492`) | temp→current table (parse `:709-731`, used `:1898-2190`) | yes |
| `oplus,pps_strategy_high_current` | `{4000,3000,2000,3000,4000,4000}` (`:493`) | same (parse `:733-752`) | yes |
| `oplus,pps_low_curr_full_strategy_temp` | `{120,200,300,440}` (`:494`) | low-current-full window (`:810-836`) | yes |
| `oplus,pps_rmos_mohm` | 8 (`:851-855`) | resistance limits | yes |
| `oplus,pps_r_limit` | `{280,200,140,90,50}` (`:844`, parse `:857-870`) | `oplus_pps_check_resistense` | yes |
| `oplus,pps_r_default` | `{85,10,10,15,15,15,15}` (`:842`, parse `:872-897`) | `r_avg` seed (`:889-900`) | yes |

Runtime re-reads after init: only `oplus_pps_check_3rd_support()` re-parses `oplus,pps_support_third`
(`:5637`), and it is called only from the autotest harness. Everything else is cached in
`chip->limits` at probe — **DT changes need a reboot, not just a module reload.**

---

## 3. Curve tables — grammar and meaning

### 3.1 Node grammar actually consumed by v1

`pps_charge_third_strategy` (`oplus_pps.c:915`) with, for `i = 0 .. pps_strategy_soc_num-2`,
a child named `pps_strategy_soc[i]` (`:212-219`):

```
strategy_soc_range_min      (i=0)   ← pps_strategy_soc[0]
strategy_soc_range_low      (1)
strategy_soc_range_mid_low  (2)
strategy_soc_range_mid      (3)
strategy_soc_range_mid_high (4)
strategy_soc_range_high     (5)
```
and inside each, for `j = 0 .. pps_strategy_temp_num-2`, a property named `pps_strategy_temp[j]`
(`:221-228`):

```
strategy_temp_little_cold   (0)    0-5 °C
strategy_temp_cool          (1)    5-12
strategy_temp_little_cool   (2)    12-20
strategy_temp_normal_low    (3)    20-35
strategy_temp_normal_high   (4)    35-43
strategy_temp_warm          (5)    43-51
```
Each property is a u32 array, **5 numbers per step**, `batt_curve_num = length/5` (`:946`).

> Note the 33 W template `oplus-chg-bh-pps-33w.dtsi` additionally has `strategy_temp_little_cool_high`
> in every child. v1 **never reads it** (`pps_strategy_temp[]` has 6 entries). Harmless, but it would
> shift nothing — v1 looks up by name, not index.

### 3.2 The 5 numbers (`struct batt_curve`, `oplus_pps.h:383-389`)

| Column | Field | Runtime use |
|---|---|---|
| 1 | `target_vbus` (mV) | **ceiling only.** `oplus_pps_set_pdo()`: `if (ask_volt > oplus_pps_get_curve_vbus()) ask_volt = curve_vbus` (`:4372-4376`). Also `oplus_pps_cp_mode_check()` compares it to 11000/20000 to pick BYPASS/SC (`:5765-5778`). Also copied to `chip->target_vbus_mv` for power display (`:988`, `:5606`). |
| 2 | `target_vbat` (mV, **per cell**) | curve advance: when `ap_batt_volt >= curve_vbat` for `PPS_CURVE_OV_CNT` counts, or `>= curve_vbat + PPS_CURVE_VBAT_DELTA_MV`, `batt_curve_index++` (`:2320-2345`, `:2357-2400`); last index ⇒ `PPS_STOP_VOTER_FULL`. |
| 3 | `target_ibus` (mA, VBUS side) | `ilimit.current_batt_curve = target_ibus * icurr_ratio` (`:1658-1659`, `:2401-2403`); over-current guard `ap_input_current > target_ibus + 250` (`:2665-2673`); over-battery-current guard uses `cp_ratio*target_ibus + 500` (`:3165-3172`, log-only). |
| 4 | `exit` (bool) | parsed into `bool exit` (`:946`) but **never read** in v1 — only printed (`:980-986`). The final step is selected by `batt_curve_index` reaching `batt_curve_num-1` (`:2398-2402`). |
| 5 | `target_time` (s) | if `> 0`: `batt_curve_time` accumulates seconds (`:2791-2793`) and forces a curve advance when `batt_curve_time > target_time + toal_curve_time` (`:2348-2356`). |

**First curve entry does NOT become the initial ask.** The initial target is
`((vbat * batt_num) / 100) * 100 + PPS_ACTION_START_DIFF_VOLT_V1` in BYPASS
(`oplus_pps.c:3926`) or `((vbat * batt_num * cp_ratio)/100)*100 + 450` in SC (`:3919`), where
`vbat = data.ap_batt_volt` (per-cell gauge mV, `:1454`) — i.e. **≈ 8.8 V + 0.3 V for a 2S pack**.
`target_vbus_mv` (from curve[0][0][0]) is used only by `oplus_pps_get_adapter_power()` (`:5606`).

### 3.3 soc/temp list parsing

`pps_strategy_temp_num` = element count of `oplus,pps_charge_strategy_temp` (cap 7, `:755-768`);
`pps_strategy_soc_num` from `oplus,pps_charge_strategy_soc` (cap 7, `:785-798`).
The **loop bound is `num-1`** (`:917`, `:930`), so 7 elements ⇒ 6 children/props. Both counts default
to 7 when the property is absent (`:766`, `:796`) — you therefore always need the full 6×6 grid.
The same arrays also become the SOC/temp **decision thresholds**
(`pps_batt_over_low_temp`..`_high` at `:769-775`, `pps_strategy_soc_over_low`..`_high` at `:800-806`),
consumed in `oplus_pps_choose_curves` (`:1578-1640`).

### 3.4 Are `pps_charge_low_curr_full` and `pps_charge_oplus_strategy` required?

**No, neither — for a third-party-only run.**

| Node | Parse site | Required? |
|---|---|---|
| `pps_charge_low_curr_full` | `:992-1047`, missing ⇒ `-EINVAL` (non-fatal, `parse_dt` ignores rc `:1154-1159`) | third-party: `oplus_pps_check_low_curr_full()` returns immediately for `PPS_ADAPTER_THIRD` (`:3074-3075`); `full_curve_num = 0` loops are inert |
| `pps_charge_oplus_strategy` | `:1049-1136`, missing ⇒ `-EINVAL` | only read when `pps_adapter_type != THIRD` (`:1643`) — dead for a third-party charger |
| `pps_charge_strategy` (v2 grammar, already present at `oplus-chg-22811.dtsi:577`) | **no v1/v2 consumer** | inert; v1 reads only the three names above (`:915`, `:1003`, `:1059`), v2 reads them under its own `oplus,pps_charge` device (`v2/oplus_chg_pps.c:6629`, `:6643`) |

Add them anyway if you later want an OPLUS-branded PPS head; they cost nothing.

---

## 4. Ops reality check with no charge pump on 22811

`oplus_pps_cp_init()` is called unconditionally from module init
(`charger_ic/oplus_battery_sm8550.c:14105-14112`), and registers **`"cp-sc8571"`**
(`chargepump_ic/oplus_pps_cp.c:569`). `oplus_pps_ops_register` only fails if the name already exists
(`oplus_pps_ops_manager.c:86-104`), so the entry is always present. `chip_sc8571_master` stays NULL
because no i2c device probes ⇒ every `sc8571_*` accessor returns early with 0
(`chargepump_ic/oplus_sc8571_master.c:652-668`, `:771-776`, `:862`).

| Ops entry | Target function | No-CP behaviour | Blocks PPS? |
|---|---|---|---|
| `pps_pdo_select` | `oplus_chg_set_pps_config` (`:523`) | real ADSP write | no — this is the only working piece |
| `get_pps_max_cur` | `oplus_chg_pps_get_max_cur` (`:525`) | ADSP `USB_GET_PPS_MAX_CURR` (`charger_ic/oplus_battery_sm8550.c:11142-11168`) | **conditionally fatal** — return `<=0` ⇒ `target_charger_current=0` (`oplus_pps.c:2767`) ⇒ volt loop drives to `charger_volt_min`, 3 strikes ⇒ `oplus_pps_stop_volt_adjust_fail()` (`:4197-4210`) |
| `get_pps_status` | `oplus_chg_get_pps_status` (`:524`) | ADSP `USB_GET_PPS_STATUS` (`oplus_battery_sm8550.c:11060`) | no — engine short-circuits for THIRD (`oplus_pps.c:885-886`) |
| `get_vbat0_volt` | `oplus_sm8350_read_vbat0_voltage` (`:516`) — **strong** def at `oplus_battery_sm8550.c:11503` overrides the weak one in `oplus_pps_cp.c:67` | ADSP `USB_PPS_READ_VBAT0_VOLT`, else `-1`→0 | only if `support_type==1` (MCU) |
| `check_btb_temp` | `oplus_sm8350_check_btb_temp` (`:520`), strong def `oplus_battery_sm8550.c:11527` | ADSP `USB_PPS_CHECK_BTB_TEMP`; `-1` on failure | **YES — possible hard stop.** `oplus_pps_is_allow_real()` requires `check_btb_temp() > 0` within 3×10 ms (`oplus_pps.c:5476-5489`) ⇒ returns false ⇒ PPS only starts as *dummy* (`oplus_charger.c:13671-13675`). Also `<=0` counts as over-temp every cycle ⇒ `PPS_STOP_VOTER_BTB_OVER` after 8 (`:2854-2861`). **Check this first on hardware.** |
| `pps_get_authentiate` | `oplus_sm8350_pps_get_authentiate` (`:522`) | ADSP `USB_PPS_GET_AUTHENTICATE` | no — third path never calls it (`:13664-13700`) |
| `pps_cp_*` (11 entries), `pps_mos_ctrl`, `pps_get_cp_*_ibus/vbus/vac/vout/tdie`, `get_ucp_flag` | `oplus_cp_*` (`:527-553`) | all NULL-safe stubs returning 0 | **YES, indirectly, and this is the killer:** `data.ap_input_current = cp_master_ibus + cp_slave_ibus + cp_slave_b_ibus` = **0 forever** (`oplus_pps.c:1505-1507`). Consequences: (a) `oplus_3rd_pps_action_volt_change` sees `cp_ibus(0) < target-100` → `+40 mV` every cycle, monotonic ramp to `PPS_3RD_ASK_VOLT_MAX` (`:4162-4175`, `:4185`); (b) `oplus_pps_check_disconnect()` fires: `ap_input_current(0) < PPS_DISCONNECT_IOUT_MIN(300)` ⇒ after `PPS_DISCONNECT_IOUT_CNT(4)` ⇒ `PPS_STOP_VOTER_DISCONNECT_OVER` (`:3443-3461`, `oplus_pps.h:84-85`) |
| `set_mcu_pps_mode` / `get_mcu_pps_mode` | `oplus_op10_*` (`:514-515`) | these symbols **do exist on 8550**: `vooc_ic/oplus_op10.o` is unconditional in `v1/Makefile:124` (and `chargepump_ic/oplus_pps_cp.o` is in the `CONFIG_OPLUS_SM8550_CHARGER` list, `v1/Makefile:196-212`), so no link error and no runtime harm — `oplus_op10_set_mcu_pps_mode()` / `_get_` return early when `the_chip == NULL` (`vooc_ic/oplus_op10.c:902-922`), and `oplus_pps_start()` calls it unconditionally (`oplus_pps.c:5081`) |
| *unset* entries (`pps_get_cp_vbat`, `pps_get_cp_master_info`, `pps_get_cp_slave_support/status`, `pps_cp_master_kick_dog`, `pps_cp_slave_kick_dog`, `pps_cp_slave_hardware_init/cfg_mode_init/reset`) | — | — | no — every call site NULL-checks first (`:1368`, `:1378`, `:1388`, `:1398-1404`, `:1420-1430`, `:2838-2842`, `:1449`) |
| *unset* `get_pps_max_volt` | — (not in the `cp-sc8571` table) | — | **hazard, not a blocker:** `oplus_pps_set_pdo()` only uses it to re-clamp when `get_pps_max_cur()` returned `<0` (`:4379-4386`) — with the pointer NULL that fallback is skipped and a negative `max_cur` is written straight into `ask_charger_current` (`:4388-4390`) → a negative (huge unsigned) `USB_SET_PPS_CURR`. Guard this in code change #3 |

**`oplus_pps_is_allow_real()` / `oplus_chg_get_mmi_value()`:** `mmi_chg` is initialised to **1**
(`oplus_charger.c:9262` in `oplus_chg_variables_init`) and only cleared by MMI factory test / user
writes (`:11710`, `:14525`, `:9065`). So `oplus_chg_get_mmi_value()` returns **1** on a normal
retail device (`:3422-3433`) — the MMI gate is **not** a blocker. The real gate inside the same
function is the `check_btb_temp()` loop described above.

---

## 5. Write path — `oplus_chg_set_pps_config()`

```
oplus_pps_set_pdo()                     oplus_pps.c:4361
  └─ ops->pps_pdo_select(ask_volt, ask_cur)          :4402
       └─ oplus_chg_set_pps_config(vbus_mv, ibus_ma) charger_ic/oplus_battery_sm8550.c:11116
            ├─ write_property_id(.., USB_SET_PPS_VOLT, vbus_mv)   :11130
            └─ write_property_id(.., USB_SET_PPS_CURR, ibus_ma)   :11131
                 └─ battery_chg_write(.., opcode = pst->opcode_set) :1051-1067, :1003-1048
```
- Property IDs: `USB_SET_PPS_VOLT` / `USB_SET_PPS_CURR` = enum slots in
  `charger_ic/oplus_battery_sm8550.h:287-288`, passed as the raw ADSP property id.
- **No clamping anywhere in this path.** The value written is exactly `chip->ask_charger_volt` /
  `ask_charger_current`, already clamped upstream by `set_pdo`: `ask_volt <= curve_vbus` (`:4372-4376`),
  `ask_cur <= get_pps_max_cur(ask_volt)` (`:4378-4390`), `ask_volt >= 5500` (`:4351-4352`).
- Return codes: `0` on success, `-1` if either write returned `<0` (`:11132-11136`).
  `battery_chg_write` returns `-ETIMEDOUT` on a 1 s(ish) glink timeout and **`0` when glink is down**
  (`:1013-1016`) — i.e. a fake success.
- **A `0` return is NOT proof the ADSP accepted the PPS request.** `handle_message()` for
  `BC_USB_STATUS_SET` only validates the frame and ACKs; the response value is discarded
  (`:2026-2031`). Only reading back `usb/voltage_now` (or `usb/voltage_max`) proves anything.
  This invalidates the naive reading of the P0.5 `pps_req` probe result.
- Preconditions for the call: `chip->ops`, `pps_support_type`, `timer.set_pdo_flag==1`
  (set on any change or every `UPDATE_PDO_TIME = 5 s`, `oplus_pps.h:42`, `:4365-4370`, `:2799-2802`).

---

## 6. Third-party gate — exact behaviour

Two independent gates, both must pass:

**Gate A — region list** (`v1/oplus_region_check.c`):
- `oplus_chg_region_check_init()` (`:202-209`) parses `oplus,pps_region_list` **only if**
  `get_regionid_from_cmdline()` found `"oplus_region="` in `/chosen/bootargs` (`:49-78`, `:207-208`).
- If bootargs lack it: `region_list_arrry[PPS_SUPPORT_REGION].elem_count == 0`;
  `find_id_in_region_list()` returns false at `list_length == 0` (`:126-127`) and also at
  `id == 0xFF` (`:118-119`).
- `third_pps_supported_from_nvid()` (`:164-178`) has one shortcut: `pps_region_list[0] == 0xFFFF
  && elem_count == 1` ⇒ **all regions allowed** (`:173-175`). This is why the DT must contain exactly
  one element `0xFFFF`.
- **`dbg_nvid` module param does NOT help.** It only overrides the *id* used in
  `find_id_in_region_list` (`:144-147`); the list is still empty, so the lookup still fails, and the
  `0xFFFF` shortcut needs `elem_count == 1`. Nothing else re-parses the lists.
- ⇒ If `/proc/cmdline` has no `oplus_region=`, the **only** fixes are code changes (§8 item 2).
  Probe it first: `adb shell su -c 'cat /proc/cmdline | tr " " "\n" | grep oplus_region'`
  and `dmesg | grep -i oplus_region`.

**Gate B — `pps_support_third`**: DT value **and** `third_pps_supported_from_nvid()` (`oplus_pps.c:512`);
`oplus_pps_check_third_pps_support()` just returns the cached flag (`:5121-5130`).

**`third_pps_priority_than_svooc()` is NOT required for a generic third-party charger.** It appears
only in the `pd_svooc == true` branch (`oplus_charger.c:13733`) — the branch taken when the ADSP sent
`BC_PD_SVOOC` (`charger_ic/oplus_battery_sm8550.c:2272-2273`), i.e. an OPPO PD-SVOOC head.
A plain PPS charger leaves `bcdev->pd_svooc == false` and takes
`oplus_charger.c:13664-13700`, which requires only `oplus_pps_check_third_pps_support()` (`:13672`).
So Gate B alone gates the start; the priority list only matters for PPS-vs-SVOOC arbitration.

Full start chain for a third-party head:
`usb_type = PD_PPS` (ADSP) → `oplus_sm8150_get_pd_type()` returns `PD_PPS_ACTIVE` only if
`oplus_pps_get_chg_status() != PPS_NOT_SUPPORT` **and** ADSP `USB_GET_PPS_TYPE` is true
(`charger_ic/oplus_battery_sm8550.c:11005-11010`) → `oplus_chg_pd_stage2()`
(`oplus_charger.c:13654-13702`) → `oplus_pps_is_allow_real()` → `oplus_pps_start(PPS_ADAPTER_THIRD)`
(`oplus_pps.c:5076`) → `oplus_pps_variables_init()` → `choose_curves()` → `cp_mode_check()` →
status `START` → `update_pps_work` (`:4825`).

---

## 7. The 55 W ceiling and every other cap

### 7.1 The clamp

```c
/* oplus_pps.h:141 */
#define PPS_3RD_ASK_VOLT_MAX 10500
/* oplus_pps.c:4212-4213 (inside oplus_3rd_pps_action_volt_change) */
if (chip->target_charger_volt > PPS_3RD_ASK_VOLT_MAX || ...)
        chip->target_charger_volt = PPS_3RD_ASK_VOLT_MAX;
```
It clamps `target_charger_volt` (the *third-party* target only), from which `ask_charger_volt` is
assigned (`:4229`). Three consecutive hits also do `ilimit.current_batt_curve = cp_ibus/100*100`
(`:4219-4225`). `PPS_VOL_MAX_V1 = 11000` (`oplus_pps.h:27`) is **not** used in this path — it is the
non-third target in `oplus_pps_action_volt_change` (`:4020-4024`).

### 7.2 Achievable power

| Case | Max VBUS | Max IBUS | Port power | ≈ battery power (η≈0.93) |
|---|---|---|---|---|
| (a) constant untouched (10500) | 10.50 V | min(5000, ADSP max-cur) | **52.5 W** | ≈ 48.8 W ⇒ ≈ 5.5 A @ 8.8 V |
| (b) changed to 11000 | 11.00 V | min(5000, ADSP max-cur) | **55.0 W** | ≈ 51 W ⇒ ≈ 5.8 A @ 8.8 V |

### 7.3 Everything else that can stop you

| Cap | Site | Effect |
|---|---|---|
| curve `target_vbus` | `oplus_pps.c:4372-4376` (via `:1171`) | second hard ceiling — DT curve must say 11000 too |
| ADSP `USB_GET_PPS_MAX_CURR` | `:4378-4390` (PDO clamp) and `:2767-2769` (loop target) | if ADSP answers <5000 at 11 V, that *is* the ceiling; `<=0` ⇒ `adjust volt fail` (`:4197-4210`) |
| `pps_strategy_normal_current` | `:4228`, `:4245` | the IBUS setpoint is this value, **not** the curve's `target_ibus` |
| `batt_num == 2` ⇒ BYPASS forced | `:5764-5771` | `cp_mode` is BYPASS whenever `batt_num != 1`, so `cp_ratio = 1` (`:1233-1251`) and `icurr_ratio = 2` (`:1214-1231`). The SC/2:1 path (which would demand ≈17.6 V) can never engage under an 11 V PPS APDO — 11 V is pure headroom, not a divider input |
| `charger_volt_min` | `:4178` `((vbat*batt_num*cp_ratio)/100)*100 + 100` | a **floor** ≈ 8.9 V at 4.4 V/cell, not a cap |
| `pps_ibat_over_third` | `:3150-3161` | stop if charge current > this (default 4000!). 55 W needs ≈ 5.8-6.2 A pack current |
| `PPS_IBAT_LOW_MIN 2000` | `:3141-3148`, `oplus_pps.h:94` | stop after 4 counts if `ap_batt_current > +2000` (discharge) |
| `pps_timeout_third` | `:3010`, `:2794` | seconds; 15000 s ≈ 4.2 h |
| ADSP `USB_PPS_CHECK_BTB_TEMP` | `:2854`, `:5477` | see §4 — can prevent start entirely |
| USB-C physics | — | 5 A requires an e-marked 5 A cable; the APDO must be a 3.3-11 V/5 A PPS APDO |
| **battery CC limit (FCC/step-chg)** | outside the PPS engine | **the real 55 W constraint**: the loop only raises VBUS while `IBUS < 5000`. If the pack CC is, say, 5 A, then IBUS at 9 V ≈ 5.0 A and the engine settles there ⇒ ≈ 45 W. Reaching 11 V/5 A requires the pack to accept ≈ 6.0-6.2 A at 8.6-8.8 V, i.e. `IBUS ≈ 2·Vcell·Ibat/(Vbus·η) = 5 A` only near 11 V. Verify the active FCC/step-chg table before blaming the PPS code |

---

## 8. Minimum module code-change list for 55 W (ordered by necessity)

| # | File:line | Exact edit | Why |
|---|---|---|---|
| 1 | `oplus_pps.h:141` | `#define PPS_3RD_ASK_VOLT_MAX 10500` → `11000` | removes the only hard VBUS ceiling for third-party (52.5 W → 55 W). Nothing else references this macro (`oplus_pps.c:4212`, `:4213`, `:4219` only) |
| 2 | `v1/oplus_region_check.c:207-208` | replace `if (get_regionid_from_cmdline()) oplus_parse_pps_region_list(chip);` with an unconditional `oplus_parse_pps_region_list(chip);` (keep the cmdline read for `region_id`) | without `oplus_region=` in bootargs the list is never parsed and third-party PPS is permanently off; `dbg_nvid` cannot work around it (§6). This is the difference between "DT-only test tells you nothing" and a real test |
| 3 | `chargepump_ic/oplus_pps_cp.c:247-257` (impl) + `:535` (ops entry) | make `oplus_cp_master_get_ibus()` fall back to the ADSP when the CP is absent, i.e. in the `chip_sc8571_master == NULL` early-return path: read `POWER_SUPPLY_PROP_CURRENT_NOW` from the `usb` psy instead of returning 0 (same property the existing `oplus_get_ibus_current()` uses, `charger_ic/oplus_battery_sm8550.c:10543-10567`). Mirror it in `oplus_cp_master_get_vbus()` (`:179-189`) with `POWER_SUPPLY_PROP_VOLTAGE_NOW` | **the blocking defect**: `data.ap_input_current` (`oplus_pps.c:1505`) is the loop's only feedback. With 0 the loop ramps VBUS to the clamp and `oplus_pps_check_disconnect()` (`:3443`) stops PPS as "disconnected". Alternative: register a second ops table (e.g. `"ap-adsp"`) from `sm8350_chg_init()` and point `oplus,pps_ops` at it — same effect, no edit to the CP file |
| 4 | `oplus_pps.c:3443-3452` (recommended) | skip `oplus_pps_check_disconnect()` when `chip->pps_adapter_type == PPS_ADAPTER_THIRD && !chip->cp.master_enable`, or raise the guard to require a real CP | belt-and-braces against a zero IBUS reading stopping a healthy PPS session |
| 5 | `oplus_pps.c:2854` / `:5477` (recommended) | if the ADSP turns out not to implement `USB_PPS_CHECK_BTB_TEMP`, treat `-1` as "unknown = allow" instead of over-temp, e.g. `if (chip->ops->check_btb_temp() == 0)` | otherwise `oplus_pps_is_allow_real()` never returns true and PPS only ever runs as a dummy |
| 6 | `charger_ic/oplus_battery_sm8550.c:11503-11525` (only if you pick `pps_support_type == 1`) | none — but verify `USB_PPS_READ_VBAT0_VOLT` returns a sane per-cell value | MCU mode derives VBAT exclusively from it |

Build/flash: `CONFIG_OPLUS_CHG=m` (`kalama_GKI.config:622`), object list at `v1/Makefile:196-212`,
product `out/vendor/oplus/kernel/charger/v1/oplus_chg.ko` → `vendor_dlkm`. DT changes ship in `dtbo`.
Do **not** rely on `scripts/build-gki.sh` (GKI `Image` only).

---

## 9. Minimum viable DT node (mechanically transcribable)

Insert inside the **first** `&battery_charger { ... }` of
`devicetree/sm8550/kernel_platform/qcom/proprietary/devicetree/oplus/oplus_chg/oplus-chg-22811.dtsi`
(block starting at `:248`; the later `&battery_charger` blocks at `:703` / `:741` only add
`io-channels` and `ic_type`). `qcom,vbatt_num = <2>` at `:427` is what forces BYPASS mode — leave it.

### 9.1 Scalars / arrays

```dts
/* ---- mandatory ---- */
oplus,pps_ops = "cp-sc8571";                  /* string  */
oplus,pps_support_type = <2>;                 /* u32, 2 = PPS_SUPPORT_2CP */
oplus,pps_support_third = <1>;                /* u32 */
oplus,pps_region_list = <0xFFFF>;             /* u32-array, EXACTLY one element */
oplus,pps_charge_strategy_soc  = <0 15 30 50 75 85 90>;    /* 7 × u32 */
oplus,pps_charge_strategy_temp = <0 50 120 200 350 440 520>;/* 7 × u32 */

/* ---- current / voltage strategy ---- */
oplus,pps_strategy_normal_current = <5000>;             /* IBUS setpoint, default 6000 */
oplus,pps_strategy_normal_bypass_limit_current = <6000>;
oplus,pps_over_high_or_low_current = <1000>;            /* default -EINVAL is dangerous */
oplus,pps_warm_allow_vol = <4000>;
oplus,pps_warm_allow_soc = <55>;
oplus,pps_timeout_third = <15000>;                      /* SECONDS */
oplus,pps_timeout_oplus = <15000>;
oplus,pps_ibat_over_third = <8000>;                     /* default 4000 would stop 55 W */
oplus,pps_ibat_over_oplus = <11400>;
oplus,pps_strategy_batt_high_temp = <420 430 435 400 415 430>;   /* 6 × u32, 0.1 °C */
oplus,pps_strategy_high_current   = <3500 3000 2000 5000 4000 3000>; /* 6 × u32, mA */
oplus,pps_low_curr_full_strategy_temp = <120 200 300 440>;       /* 4 × u32 */

/* ---- full-charge thresholds (mV per cell) ---- */
oplus,pps_full_cool_sw_vbat = <4450>;
oplus,pps_full_normal_sw_vbat = <4500>;
oplus,pps_full_normal_hw_vbat = <4510>;
oplus,pps_full_ffc_vbat = <4520>;
oplus,pps_full_warm_vbat = <4130>;
oplus,pps_full_cool_sw_vbat_third = <4450>;
oplus,pps_full_normal_sw_vbat_third = <4500>;
oplus,pps_full_normal_hw_vbat_third = <4510>;

/* ---- impedance model (copy of the 33 W template; bench-tune) ---- */
oplus,pps_r_default = <270 45 10 45 20 20 20>;   /* 7 × u32 */
oplus,pps_rmos_mohm = <5>;
oplus,pps_r_limit = <150 100 80 70 30>;          /* 5 × u32 */
oplus,pps_exit_ms = <1>;
```

Types: every numeric property above is `u32` (or `u32`-array of the stated length); `oplus,pps_ops` is
the only `string`. Note `pps_support_type` = **2**, not 1 (§1).

### 9.2 Curve subtree

Per-cell VBAT, VBUS ceiling 11000 everywhere, IBUS 5000 at low SOC tapering to 1000.
`strategy_temp_warm` uses the 4130 mV warm-full limit.

```dts
pps_charge_third_strategy {
	strategy_soc_range_min {   /* soc   0 .. 15 */
		strategy_temp_little_cold = <11000 4180 2000 0 0  11000 4400 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4180 3000 0 0  11000 4400 2500 0 0  11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4180 5000 0 0  11000 4300 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_low  = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_high = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4120 2000 0 0  11000 4120 1500 0 0  11000 4130 1000 1 0>;
	};
	strategy_soc_range_low {   /* soc  15 .. 30 */
		strategy_temp_little_cold = <11000 4180 2000 0 0  11000 4400 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4180 3000 0 0  11000 4400 2500 0 0  11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4180 5000 0 0  11000 4300 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2000 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_low  = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_high = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4120 2000 0 0  11000 4120 1500 0 0  11000 4130 1000 1 0>;
	};
	strategy_soc_range_mid_low { /* soc 30 .. 50 */
		strategy_temp_little_cold = <11000 4400 2000 0 0  11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4400 3000 0 0  11000 4450 2500 0 0  11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4300 5000 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_low  = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_high = <11000 4280 5000 0 0  11000 4450 4500 0 0  11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4120 2000 0 0  11000 4120 1500 0 0  11000 4130 1000 1 0>;
	};
	strategy_soc_range_mid {   /* soc  50 .. 75 */
		strategy_temp_little_cold = <11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4450 2000 0 0  11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_low  = <11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_high = <11000 4450 4000 0 0  11000 4450 3500 0 0  11000 4450 3000 0 0  11000 4450 2500 0 0  11000 4450 2000 0 0  11000 4500 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4130 1000 1 0>;
	};
	strategy_soc_range_mid_high { /* soc 75 .. 85 */
		strategy_temp_little_cold = <11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4450 1500 0 0  11000 4450 1000 1 0>;
		strategy_temp_normal_low  = <11000 4450 2000 0 0  11000 4450 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_normal_high = <11000 4450 2000 0 0  11000 4450 1500 0 0  11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4130 1000 1 0>;
	};
	strategy_soc_range_high {  /* soc  85 .. 90 */
		strategy_temp_little_cold = <11000 4450 1000 1 0>;
		strategy_temp_cool        = <11000 4450 1000 1 0>;
		strategy_temp_little_cool = <11000 4450 1000 1 0>;
		strategy_temp_normal_low  = <11000 4450 1000 1 0>;
		strategy_temp_normal_high = <11000 4500 1000 1 0>;
		strategy_temp_warm        = <11000 4130 1000 1 0>;
	};
};
```

Sanity: per property the u32 count is a multiple of 5, and each soc node has all 6 temp properties
(v1 aborts the whole parse at the first missing child/prop, `oplus_pps.c:916-935`).

### 9.3 Which numbers are guesses (bench-tune)

| Group | Status |
|---|---|
| `target_vbus = 11000` | **derived**, not guessed: matches `PPS_VOL_MAX_V1` (`oplus_pps.h:27`) and 24821/22825 precedents |
| IBUS ladder 5000 → 1000 | **guessed**; must be validated against the pack CC and the 5 A cable |
| every `target_vbat` (4180/4280/4300/4400/4450/4500/4120/4130) | **guessed** — copy 33 W template shape, adapted to 4500 mV full / 4130 warm |
| `oplus,pps_charge_strategy_soc` / `_temp` breakpoints | **guessed** (33 W uses `0 20 35 55 75 85 90` / `0 50 120 160 210 350 440 510`; v1 has 6 temp buckets, so 7 values here) |
| `pps_strategy_batt_high_temp` / `_high_current` | **guessed**, derived from the compiled defaults, re-scaled for 5 A |
| `pps_ibat_over_third = 8000` | **guessed** — must exceed pack current at 55 W (≈5.8-6.2 A) with margin, but stay a safety limit |
| `pps_timeout_third = 15000` (s) | guessed; 24821-style magnitude (`17537`) is fine |
| `pps_r_default` / `pps_rmos_mohm` / `pps_r_limit` | copied from the 33 W template; the 8550-bypass topology differs ⇒ **must be re-measured** |
| `strategy_temp_*` numeric profile for warm/cold | guessed; OPPO always derates below 12 °C and above 43 °C |

---

## 10. Cheap checkpoints, in order — each one can invalidate everything above

1. `cat /proc/cmdline | grep oplus_region` and `dmesg | grep -i oplus_region` (Gate A, §6).
2. `dmesg | grep -E "pps_ops_name|oplus,pps_support_type|chip->pps_support_third|Can not find third"`
   after flashing the DT — proves parse succeeded and the ops name resolved.
3. `dmesg | grep -E "pps btb > 80|PPS vbat0 volt|PPS max curr|PPS status ="` —
   tells you whether ADSP implements `USB_PPS_CHECK_BTB_TEMP`, `USB_PPS_READ_VBAT0_VOLT`,
   `USB_GET_PPS_MAX_CURR`, `USB_GET_PPS_STATUS`. Any of these returning `-1` changes the verdict.
4. `cat /sys/class/power_supply/usb/voltage_now usb/current_now` while a PPS head is attached and
   the `pps_req` probe writes 11000/5000 — the only way to tell a real PPS accept from a glink ACK.
5. `dmesg | grep -E "ask_volt, ask_cur|3rd pps chargering volt|output_low|adjust volt fail|disconnect"`
   — shows whether the loop converges or trips.
