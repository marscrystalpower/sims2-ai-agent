// TS2Bridge v1.33 read-only pause/mode probe.
// Built for the SHA-256 ee56ec6209aac4a7796370d3ca75a825809303393a036a0ba9a242701f2ab2b6.
// This is experimental native code. It never writes to game memory or saves.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GAME_BASE 0x00400000u
#define ROOT_RVA 0x00399A0Du
#define GLOBAL_WRAPPER_RVA 0x004FD540u
#define OBJECT_LOOKUP_RVA 0x004FE4D0u
#define RELATION_GETTER_RVA 0x004FD080u

static volatile LONG stopping;
static volatile LONG pending;
static char log_path[MAX_PATH];
static char state_path[MAX_PATH], state_temp_path[MAX_PATH];
static char events_path[MAX_PATH];
static char relationship_path[MAX_PATH], relationship_temp_path[MAX_PATH];
static char relationship_target_path[MAX_PATH];
static int relationship_target_nid;
static HINSTANCE plugin_module;
static HMODULE game_module;
static HWND game_window;
static HHOOK message_hook;
static UINT sample_message;
static DWORD game_thread;

enum { HUNGER_WARNING = 0, HUNGER_URGENT = -30,
       WARNING_RECOVERED = 20, URGENT_RECOVERED = -10, POLL_MS = 5000,
       BLADDER_WARNING = -20, BLADDER_URGENT = -40,
       BLADDER_WARNING_RECOVERED = 0, BLADDER_URGENT_RECOVERED = -20,
       BLADDER_MOTIVE_SLOT = 3,
       ENERGY_WARNING = 0, ENERGY_URGENT = -40,
       ENERGY_WARNING_RECOVERED = 20, ENERGY_URGENT_RECOVERED = -20,
       ENERGY_MOTIVE_SLOT = 0,
       MAX_PERSONS = 32, MAX_HOUSEHOLD_RELATIONS = 8,
       MAX_TRACKED_RELATIONS = 72, CAREER_UNAVAILABLE = -32768 };
static const int extra_motive_ids[] = {5, 6, 8, 9, 13, 14, 15};
static const char *const extra_motive_names[] = {
    "energy", "comfort", "hygiene", "bladder", "environment", "social", "fun"};
static const int skill_person_data_ids[] = {9, 10, 11, 12, 15, 17, 18};
static const char *const skill_names[] = {
    "cleaning", "cooking", "charisma", "mechanical", "creativity", "body", "logic"};
static const int personality_person_data_ids[] = {7, 6, 3, 5, 2};
static const char *const personality_names[] = {
    "neat", "outgoing", "active", "playful", "nice"};

typedef struct Snapshot {
    int16_t oid, nid, hour, minute, current_family, family_number;
    int16_t paused_probe_raw, mode_probe_raw;
    float hunger;
} Snapshot;

typedef struct RosterSim {
    int16_t oid, nid, family_number;
    float hunger;
    int16_t school_grade_raw, job_level_raw, job_performance_raw, age_raw, gender_raw;
    int16_t ghost_flags_raw, body_flags_raw;
    int16_t preference_male_raw, preference_female_raw;
    int16_t skills_raw[7];
    int16_t hobby_enthusiasm_probe_raw[10], predestined_hobby_probe_raw;
    int16_t interest_probe_raw[18];
    int16_t personality_raw[5], aspiration_raw;
    int16_t lta_raw[3];
    float extra_motives[7];
    uint8_t extra_motives_valid;
    BOOL was_household_member;
    BOOL new_household_identity;
} RosterSim;

typedef struct TrackedSim {
    RosterSim last;
    BOOL warning_active, urgent_active;
    BOOL bladder_warning_active, bladder_urgent_active;
    BOOL energy_warning_active, energy_urgent_active;
    unsigned missed_scans;
} TrackedSim;

static Snapshot previous;
static BOOL have_previous, hook_seen, unavailable_reported, roster_initialized;
static TrackedSim tracked[MAX_PERSONS];
static unsigned tracked_count;
static int16_t known_household_nids[MAX_PERSONS];
static unsigned known_household_count;
static DWORD last_roster_summary;
static BOOL state_written, state_error_reported;
static BOOL events_written, events_error_reported;
static BOOL funds_baseline_valid;
static int funds_baseline_family, funds_baseline_amount;

static BOOL known_household_member(int16_t nid)
{
    for (unsigned i = 0; i < known_household_count; ++i)
        if (known_household_nids[i] == nid) return TRUE;
    return FALSE;
}

static BOOL read_family_funds(int family_id, int *funds);

static void format_needs(char *out, size_t cap, const RosterSim *sim)
{
    size_t used;
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"needsRaw\":null");
        return;
    }
    used = (size_t)snprintf(out, cap, ",\"needsRaw\":{\"hunger\":%.2f", (double)sim->hunger);
    for (unsigned i = 0; i < 7 && used < cap; ++i) {
        int wrote;
        if (sim->extra_motives_valid & (1u << i))
            wrote = snprintf(out + used, cap - used, ",\"%s\":%.2f",
                             extra_motive_names[i], (double)sim->extra_motives[i]);
        else
            wrote = snprintf(out + used, cap - used, ",\"%s\":null", extra_motive_names[i]);
        if (wrote < 0 || (size_t)wrote >= cap - used) break;
        used += (size_t)wrote;
    }
    if (used < cap) snprintf(out + used, cap - used, "}");
}

static void format_gender(char *out, size_t cap, const RosterSim *sim)
{
    if (sim->gender_raw == CAREER_UNAVAILABLE)
        snprintf(out, cap, ",\"genderRaw\":null");
    else
        snprintf(out, cap, ",\"genderRaw\":%d", sim->gender_raw);
}

static void format_preference(char *out, size_t cap, const RosterSim *sim)
{
    if (sim->preference_male_raw < -1000 || sim->preference_male_raw > 1000 ||
        sim->preference_female_raw < -1000 || sim->preference_female_raw > 1000) {
        snprintf(out, cap, ",\"genderPreferenceRaw\":null");
        return;
    }
    snprintf(out, cap, ",\"genderPreferenceRaw\":{\"male\":%d,\"female\":%d}",
             sim->preference_male_raw, sim->preference_female_raw);
}

static void format_skills(char *out, size_t cap, const RosterSim *sim)
{
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"skillsRaw\":null");
        return;
    }
    size_t used = (size_t)snprintf(out, cap, ",\"skillsRaw\":{");
    for (unsigned i = 0; i < 7 && used < cap; ++i) {
        int wrote = snprintf(out + used, cap - used, "%s\"%s\":%d",
                             i ? "," : "", skill_names[i], sim->skills_raw[i]);
        if (wrote < 0 || (size_t)wrote >= cap - used) return;
        used += (size_t)wrote;
    }
    if (used < cap) snprintf(out + used, cap - used, "}");
}

// FreeTime candidate person-data words 0xCC..0xD5 and 0xD7. Keep the
// numeric slot names until the live panel and SimPE verify the mapping.
static void format_hobby_probe(char *out, size_t cap, const RosterSim *sim)
{
    size_t used;
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"hobbyEnthusiasmProbeRaw\":null,\"predestinedHobbyProbeRaw\":null");
        return;
    }
    used = (size_t)snprintf(out, cap, ",\"hobbyEnthusiasmProbeRaw\":{");
    for (unsigned i = 0; i < 10 && used < cap; ++i) {
        int wrote = snprintf(out + used, cap - used, "%s\"0x%02X\":%d",
                             i ? "," : "", 0xCC + i, sim->hobby_enthusiasm_probe_raw[i]);
        if (wrote < 0 || (size_t)wrote >= cap - used) return;
        used += (size_t)wrote;
    }
    if (used < cap)
        snprintf(out + used, cap - used, "},\"predestinedHobbyProbeRaw\":%d",
                 sim->predestined_hobby_probe_raw);
}

// SDSC interest words at offsets 0x104..0x126 map to person-data
// indices 0x7C..0x8D. Keep numeric slots until the live panel confirms them.
static void format_interest_probe(char *out, size_t cap, const RosterSim *sim)
{
    size_t used;
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"interestProbeRaw\":null");
        return;
    }
    used = (size_t)snprintf(out, cap, ",\"interestProbeRaw\":{");
    for (unsigned i = 0; i < 18 && used < cap; ++i) {
        int wrote = snprintf(out + used, cap - used, "%s\"0x%02X\":%d",
                             i ? "," : "", 0x7C + i, sim->interest_probe_raw[i]);
        if (wrote < 0 || (size_t)wrote >= cap - used) return;
        used += (size_t)wrote;
    }
    if (used < cap) snprintf(out + used, cap - used, "}");
}

static void format_personality(char *out, size_t cap, const RosterSim *sim)
{
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"personalityRaw\":null,\"aspirationRaw\":null");
        return;
    }
    size_t used = (size_t)snprintf(out, cap, ",\"personalityRaw\":{");
    for (unsigned i = 0; i < 5 && used < cap; ++i) {
        int wrote = snprintf(out + used, cap - used, "%s\"%s\":%d",
                             i ? "," : "", personality_names[i], sim->personality_raw[i]);
        if (wrote < 0 || (size_t)wrote >= cap - used) return;
        used += (size_t)wrote;
    }
    if (used < cap)
        snprintf(out + used, cap - used, "},\"aspirationRaw\":%d", sim->aspiration_raw);
}

static void format_lta(char *out, size_t cap, const RosterSim *sim)
{
    if (sim->school_grade_raw == CAREER_UNAVAILABLE) {
        snprintf(out, cap, ",\"lifetimeAspirationRaw\":null");
        return;
    }
    snprintf(out, cap,
             ",\"lifetimeAspirationRaw\":{\"meter\":%d,\"unlock\":%d,\"spent\":%d}",
             sim->lta_raw[0], sim->lta_raw[1], sim->lta_raw[2]);
}

static void log_line(const char *message)
{
    HANDLE file;
    DWORD written;
    SYSTEMTIME now;
    char line[512];
    int length;
    GetLocalTime(&now);
    length = snprintf(line, sizeof(line), "[%02u:%02u:%02u] %s\r\n",
                      now.wHour, now.wMinute, now.wSecond, message);
    if (length <= 0 || length >= (int)sizeof(line)) return;
    file = CreateFileA(log_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                       NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, line, (DWORD)length, &written, NULL);
    CloseHandle(file);
}

// JSON Lines: each line is an independent observation that a client can tail.
// NID is the stable game identity; OID can be recycled for another visitor.
static void emit_event(const char *type, RosterSim sim, unsigned loaded_count,
                       const RosterSim *before)
{
    char json[1600], career[160], prior[128], life[48], needs[280], gender[32];
    char aspiration[48], lta[112];
    HANDLE file;
    SYSTEMTIME utc;
    DWORD written;
    int length;
    GetSystemTime(&utc);
    if (sim.school_grade_raw != CAREER_UNAVAILABLE)
        snprintf(career, sizeof(career), ",\"schoolGradeRaw\":%d,\"jobLevelRaw\":%d,\"jobPerformanceRaw\":%d,\"ageRaw\":%d",
                 sim.school_grade_raw, sim.job_level_raw, sim.job_performance_raw, sim.age_raw);
    else
        snprintf(career, sizeof(career), ",\"schoolGradeRaw\":null,\"jobLevelRaw\":null,\"jobPerformanceRaw\":null,\"ageRaw\":null");
    if (sim.ghost_flags_raw != CAREER_UNAVAILABLE)
        snprintf(life, sizeof(life), ",\"ghostFlagsRaw\":%d", sim.ghost_flags_raw);
    else
        snprintf(life, sizeof(life), ",\"ghostFlagsRaw\":null");
    format_needs(needs, sizeof(needs), &sim);
    format_gender(gender, sizeof(gender), &sim);
    if (sim.aspiration_raw == CAREER_UNAVAILABLE)
        snprintf(aspiration, sizeof(aspiration), ",\"aspirationRaw\":null");
    else
        snprintf(aspiration, sizeof(aspiration), ",\"aspirationRaw\":%d", sim.aspiration_raw);
    format_lta(lta, sizeof(lta), &sim);
    prior[0] = '\0';
    if (before) {
        if (strcmp(type, "family_number_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousFamilyNumber\":%d",
                     before->family_number);
        else if (strcmp(type, "ghost_flags_raw_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousGhostFlagsRaw\":%d",
                     before->ghost_flags_raw);
        else if (strcmp(type, "body_flags_raw_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousBodyFlagsRaw\":%u,\"bodyFlagsRaw\":%u",
                     (unsigned)(uint16_t)before->body_flags_raw,
                     (unsigned)(uint16_t)sim.body_flags_raw);
        else if (strcmp(type, "age_raw_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousAgeRaw\":%d",
                     before->age_raw);
        else if (strcmp(type, "aspiration_raw_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousAspirationRaw\":%d",
                     before->aspiration_raw);
        else if (strcmp(type, "lifetime_benefit_spent_changed") == 0)
            snprintf(prior, sizeof(prior), ",\"previousLifetimeBenefitSpentRaw\":%d",
                     before->lta_raw[2]);
        else
            snprintf(prior, sizeof(prior), ",\"previousSchoolGradeRaw\":%d,\"previousJobLevelRaw\":%d,\"previousJobPerformanceRaw\":%d",
                     before->school_grade_raw, before->job_level_raw, before->job_performance_raw);
    }
    length = snprintf(json, sizeof(json),
        "{\"schema\":1,\"bridge\":\"1.33\",\"sampledUtc\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\","
        "\"pid\":%lu,\"gameHour\":%d,\"gameMinute\":%d,\"type\":\"%s\","
        "\"nid\":%u,\"oid\":%u,\"hunger\":%.2f,\"loadedCount\":%u,"
        "\"currentFamily\":%d,\"familyNumber\":%d,\"matchesCurrentFamily\":%s,"
        "\"wasHouseholdMemberThisLot\":%s%s%s%s%s%s%s%s}\n",
        utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond,
        (unsigned long)GetCurrentProcessId(), (int)previous.hour, (int)previous.minute,
        type, (unsigned)(uint16_t)sim.nid, (unsigned)(uint16_t)sim.oid,
        (double)sim.hunger, loaded_count,
        (int)previous.current_family, (int)sim.family_number,
        previous.current_family > 0 && sim.family_number == previous.current_family ? "true" : "false",
        sim.was_household_member ? "true" : "false", career, life, gender,
        needs, aspiration, lta, prior);
    if (length <= 0 || length >= (int)sizeof(json)) return;
    file = CreateFileA(events_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                       NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) goto fail;
    if (!WriteFile(file, json, (DWORD)length, &written, NULL) || written != (DWORD)length) {
        CloseHandle(file);
        goto fail;
    }
    CloseHandle(file);
    if (!events_written) log_line("EVENT_FILE active: mods\\TS2Bridge-events.jsonl (append-only observations).");
    events_written = TRUE;
    events_error_reported = FALSE;
    return;
fail:
    if (!events_error_reported) log_line("EVENT_FILE write failed; check mods folder permissions.");
    events_error_reported = TRUE;
}

// Household balance changes have no individual Sim as their subject.
static void emit_funds_change(int family, int before, int after)
{
    char json[384];
    HANDLE file;
    SYSTEMTIME utc;
    DWORD written;
    int length;
    GetSystemTime(&utc);
    length = snprintf(json, sizeof(json),
        "{\"schema\":1,\"bridge\":\"1.33\",\"sampledUtc\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\","
        "\"pid\":%lu,\"gameHour\":%d,\"gameMinute\":%d,\"type\":\"household_funds_changed\","
        "\"currentFamily\":%d,\"previousHouseholdFunds\":%d,\"householdFunds\":%d,\"delta\":%d}\n",
        utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond,
        (unsigned long)GetCurrentProcessId(), (int)previous.hour, (int)previous.minute,
        family, before, after, after - before);
    if (length <= 0 || length >= (int)sizeof(json)) return;
    file = CreateFileA(events_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                       NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) goto fail;
    if (!WriteFile(file, json, (DWORD)length, &written, NULL) || written != (DWORD)length) {
        CloseHandle(file);
        goto fail;
    }
    CloseHandle(file);
    if (!events_written) log_line("EVENT_FILE active: mods\\TS2Bridge-events.jsonl (append-only observations).");
    events_written = TRUE;
    events_error_reported = FALSE;
    return;
fail:
    if (!events_error_reported) log_line("EVENT_FILE write failed; check mods folder permissions.");
    events_error_reported = TRUE;
}

// Publish a complete snapshot by replacing the prior file after closing the
// temporary file. Readers see one whole sample, never a partially written one.
static void write_state(const RosterSim *sims, unsigned count, BOOL available)
{
    char json[49152];
    size_t used;
    HANDLE file;
    DWORD written;
    SYSTEMTIME utc;
    BOOL okay;
    GetSystemTime(&utc);
    used = (size_t)snprintf(json, sizeof(json),
        "{\"schema\":1,\"bridge\":\"1.33\",\"sampledUtc\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\",\"status\":\"%s\"",
        utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond,
        available ? "lot" : "unavailable");
    if (used >= sizeof(json)) return;
    if (available) {
        int funds = 0;
        BOOL funds_valid = read_family_funds((int)previous.current_family, &funds);
        int n = snprintf(json + used, sizeof(json) - used,
            ",\"gameHour\":%d,\"gameMinute\":%d,\"selectedNid\":%u,\"selectedOid\":%u,"
            "\"currentFamily\":%d,\"selectedFamilyNumber\":%d,"
            "\"pausedProbeRaw\":%d,\"gameModeProbeRaw\":%d,\"householdFunds\":",
            (int)previous.hour, (int)previous.minute,
            (unsigned)(uint16_t)previous.nid, (unsigned)(uint16_t)previous.oid,
            (int)previous.current_family, (int)previous.family_number,
            (int)previous.paused_probe_raw, (int)previous.mode_probe_raw);
        if (n < 0 || (size_t)n >= sizeof(json) - used) return;
        used += (size_t)n;
        n = snprintf(json + used, sizeof(json) - used,
                     funds_valid ? "%d,\"fundsProbeRaw\":%d,\"sims\":[" :
                                   "null,\"fundsProbeRaw\":null,\"sims\":[", funds, funds);
        if (n < 0 || (size_t)n >= sizeof(json) - used) return;
        used += (size_t)n;
        if (funds_valid) {
            if (funds_baseline_valid && funds_baseline_family == previous.current_family &&
                funds_baseline_amount != funds)
                emit_funds_change(previous.current_family, funds_baseline_amount, funds);
            funds_baseline_valid = TRUE;
            funds_baseline_family = previous.current_family;
            funds_baseline_amount = funds;
        } else {
            funds_baseline_valid = FALSE;
        }
        for (unsigned i = 0; i < count; ++i) {
            char career[128];
            char life[96], needs[280], gender[32], preference[88], skills[192], hobby[256], interests[300], personality[176], lta[112];
            if (sims[i].school_grade_raw != CAREER_UNAVAILABLE)
                snprintf(career, sizeof(career), ",\"schoolGradeRaw\":%d,\"jobLevelRaw\":%d,\"jobPerformanceRaw\":%d,\"ageRaw\":%d",
                         sims[i].school_grade_raw, sims[i].job_level_raw,
                         sims[i].job_performance_raw, sims[i].age_raw);
            else
                snprintf(career, sizeof(career), ",\"schoolGradeRaw\":null,\"jobLevelRaw\":null,\"jobPerformanceRaw\":null,\"ageRaw\":null");
            if (sims[i].ghost_flags_raw != CAREER_UNAVAILABLE)
                snprintf(life, sizeof(life), ",\"ghostFlagsRaw\":%d", sims[i].ghost_flags_raw);
            else
                snprintf(life, sizeof(life), ",\"ghostFlagsRaw\":null");
            if (sims[i].body_flags_raw != CAREER_UNAVAILABLE)
                snprintf(life + strlen(life), sizeof(life) - strlen(life),
                         ",\"bodyFlagsRaw\":%u", (unsigned)(uint16_t)sims[i].body_flags_raw);
            else
                snprintf(life + strlen(life), sizeof(life) - strlen(life),
                         ",\"bodyFlagsRaw\":null");
            format_needs(needs, sizeof(needs), &sims[i]);
            format_gender(gender, sizeof(gender), &sims[i]);
            format_preference(preference, sizeof(preference), &sims[i]);
            format_skills(skills, sizeof(skills), &sims[i]);
            format_hobby_probe(hobby, sizeof(hobby), &sims[i]);
            format_interest_probe(interests, sizeof(interests), &sims[i]);
            format_personality(personality, sizeof(personality), &sims[i]);
            format_lta(lta, sizeof(lta), &sims[i]);
            n = snprintf(json + used, sizeof(json) - used,
                "%s{\"nid\":%u,\"oid\":%u,\"hunger\":%.2f,"
                "\"familyNumber\":%d,\"matchesCurrentFamily\":%s,"
                "\"wasHouseholdMemberThisLot\":%s%s%s%s%s%s%s%s%s%s%s}", i ? "," : "",
                (unsigned)(uint16_t)sims[i].nid, (unsigned)(uint16_t)sims[i].oid,
                (double)sims[i].hunger, (int)sims[i].family_number,
                previous.current_family > 0 && sims[i].family_number == previous.current_family ? "true" : "false",
                sims[i].was_household_member ? "true" : "false", career, life, gender, preference,
                needs, skills, hobby, interests, personality, lta);
            if (n < 0 || (size_t)n >= sizeof(json) - used) return;
            used += (size_t)n;
        }
        if (used + 1 >= sizeof(json)) return;
        json[used++] = ']';
    } else {
        funds_baseline_valid = FALSE;
    }
    if (used + 3 >= sizeof(json)) return;
    json[used++] = '}';
    json[used++] = '\n';
    file = CreateFileA(state_temp_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) goto fail;
    okay = WriteFile(file, json, (DWORD)used, &written, NULL) && written == used;
    if (!CloseHandle(file) || !okay) goto fail;
    if (!MoveFileExA(state_temp_path, state_path, MOVEFILE_REPLACE_EXISTING)) goto fail;
    if (!state_written) log_line("STATE_FILE active: mods\\TS2Bridge-state.json (atomic local snapshot).");
    state_written = TRUE;
    state_error_reported = FALSE;
    return;
fail:
    if (!state_error_reported) log_line("STATE_FILE write failed; check mods folder permissions.");
    state_error_reported = TRUE;
}

static BOOL readable(const void *pointer, size_t length)
{
    MEMORY_BASIC_INFORMATION info;
    uintptr_t start = (uintptr_t)pointer;
    uintptr_t end;
    DWORD p;
    if (!pointer || length > UINTPTR_MAX - start) return FALSE;
    end = start + length;
    if (!VirtualQuery(pointer, &info, sizeof(info)) || info.State != MEM_COMMIT) return FALSE;
    p = info.Protect & 0xff;
    if ((info.Protect & PAGE_GUARD) || p == PAGE_NOACCESS) return FALSE;
    return end <= (uintptr_t)info.BaseAddress + info.RegionSize;
}

static BOOL executable(const void *pointer)
{
    MEMORY_BASIC_INFORMATION info;
    DWORD p;
    if (!VirtualQuery(pointer, &info, sizeof(info)) || info.State != MEM_COMMIT) return FALSE;
    p = info.Protect & 0xff;
    return !(info.Protect & PAGE_GUARD) &&
           (p == PAGE_EXECUTE || p == PAGE_EXECUTE_READ ||
            p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY);
}

static BOOL fingerprint(HMODULE game)
{
    static const unsigned char getter_bytes[] = {0x8b,0x44,0x24,0x04,0x56,0x8b,0x70,0xfc};
    static const unsigned char root_bytes[]   = {0x55,0x8b,0xec,0x51,0xff,0x25};
    static const unsigned char lookup_bytes[] = {0x6a,0xff,0xe9};
    static const unsigned char relation_bytes[] = {0x83,0xec,0x08,0x8b,0x44,0x24,0x0c,0x53,0x55,0x56};
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)game;
    const IMAGE_NT_HEADERS32 *nt;
    if ((uintptr_t)game != GAME_BASE || !readable(dos, sizeof(*dos)) || dos->e_magic != IMAGE_DOS_SIGNATURE)
        return FALSE;
    if (dos->e_lfanew < 0 || dos->e_lfanew > 4096) return FALSE;
    nt = (const IMAGE_NT_HEADERS32 *)((const char *)game + dos->e_lfanew);
    if (!readable(nt, sizeof(*nt)) || nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->FileHeader.TimeDateStamp != 0x48f12b6f ||
        nt->OptionalHeader.SizeOfImage != 0x02c114e0) return FALSE;
    return readable((const char *)game + GLOBAL_WRAPPER_RVA, sizeof(getter_bytes)) &&
           readable((const char *)game + ROOT_RVA, sizeof(root_bytes)) &&
           readable((const char *)game + OBJECT_LOOKUP_RVA, sizeof(lookup_bytes)) &&
           readable((const char *)game + RELATION_GETTER_RVA, sizeof(relation_bytes)) &&
           memcmp((const char *)game + GLOBAL_WRAPPER_RVA, getter_bytes, sizeof(getter_bytes)) == 0 &&
           memcmp((const char *)game + ROOT_RVA, root_bytes, sizeof(root_bytes)) == 0 &&
           memcmp((const char *)game + OBJECT_LOOKUP_RVA, lookup_bytes, sizeof(lookup_bytes)) == 0 &&
           memcmp((const char *)game + RELATION_GETTER_RVA, relation_bytes, sizeof(relation_bytes)) == 0;
}

// The native game wrapper GetSimulatorGlobal at 0x008fd540 obtains a
// simulator interface from the root (virtual method +0x0c) and reads a
// signed 16-bit global via its virtual method at +0x3c. Keep calls minimal.
typedef void *(__cdecl *GetRoot)(void);
typedef void *(__attribute__((thiscall)) *GetSimulator)(void *self);
typedef int16_t (__attribute__((thiscall)) *GetGlobal)(void *self, int index);
typedef void *(__cdecl *LookupPerson)(int object_id);
typedef int16_t (__attribute__((thiscall)) *GetPersonData)(void *self, int field, int mode);
typedef float (__attribute__((thiscall)) *GetMotive)(void *self, int motive);
typedef void *(__attribute__((thiscall)) *GetObjectManager)(void *self);
typedef void *(__attribute__((thiscall)) *GetObjectVector)(void *self);
typedef void *(__attribute__((thiscall)) *GetPersonInterface)(void *self);
typedef unsigned char (__attribute__((thiscall)) *HasObjectFlag)(void *self, unsigned flag);
typedef int16_t (__attribute__((thiscall)) *GetObjectField)(void *self, int field);
typedef int16_t (__attribute__((thiscall)) *GetObjectID)(void *self);
typedef void *(__attribute__((thiscall)) *GetNeighborhood)(void *self);
typedef void *(__attribute__((thiscall)) *GetNeighbor)(void *self, int nid, int mode);
typedef void *(__attribute__((thiscall)) *GetRelationMatrix)(void *self);
typedef unsigned (__attribute__((thiscall)) *GetRelationCount)(void *self, int other_nid);
typedef int (__attribute__((thiscall)) *GetRelationValue)(void *self, int other_nid, int index);
typedef void *(__attribute__((thiscall)) *GetFamilyManager)(void *self);
typedef void *(__attribute__((thiscall)) *LookupFamily)(void *self, int family_id);
typedef int (__attribute__((thiscall)) *GetFamilyInteger)(void *self);

// The game's familyFunds handler at 0x007DC120 walks root vtable[9],
// manager vtable[11](family ID), and family vtable[53] (getFunds).
// The registered family getId wrapper at 0x00901BB0 uses vtable[41].
// Call only getters, and reject a family whose returned ID differs from
// the lot's current family global before reading any balance.
static BOOL read_family_funds(int family_id, int *funds)
{
    void *root, *manager, *family;
    void **vt;
    GetFamilyManager get_manager;
    LookupFamily lookup;
    GetFamilyInteger get_id, get_funds;
    int value;
    if (family_id <= 0 || family_id >= 32767) return FALSE;
    root = ((GetRoot)((uintptr_t)game_module + ROOT_RVA))();
    if (!readable(root, sizeof(void *))) return FALSE;
    vt = *(void ***)root;
    if (!readable(vt, 10 * sizeof(void *))) return FALSE;
    get_manager = (GetFamilyManager)vt[9];
    if (!executable((const void *)get_manager)) return FALSE;
    manager = get_manager(root);
    if (!readable(manager, sizeof(void *))) return FALSE;
    vt = *(void ***)manager;
    if (!readable(vt, 12 * sizeof(void *))) return FALSE;
    lookup = (LookupFamily)vt[11];
    if (!executable((const void *)lookup)) return FALSE;
    family = lookup(manager, family_id);
    if (!readable(family, sizeof(void *))) return FALSE;
    vt = *(void ***)family;
    if (!readable(vt, 54 * sizeof(void *))) return FALSE;
    get_id = (GetFamilyInteger)vt[41];
    get_funds = (GetFamilyInteger)vt[53];
    if (!executable((const void *)get_id) ||
        !executable((const void *)get_funds) ||
        get_id(family) != family_id) return FALSE;
    value = get_funds(family);
    if (value < 0 || value > 2000000000) return FALSE;
    *funds = value;
    return TRUE;
}

typedef struct RelationRaw {
    int viewer, other, values[10];
    unsigned count;
} RelationRaw;

typedef struct TrackedRelation {
    int viewer, other, family, daily, lifetime, state_bits;
    BOOL valid;
} TrackedRelation;
static TrackedRelation tracked_relations[MAX_TRACKED_RELATIONS];

static void emit_relation_change(const char *kind, const TrackedRelation *before,
                                  const RelationRaw *after, unsigned lost_bits)
{
    char json[640];
    SYSTEMTIME utc;
    HANDLE file;
    DWORD written;
    int length;
    GetSystemTime(&utc);
    length = snprintf(json, sizeof(json),
        "{\"schema\":1,\"bridge\":\"1.33\",\"sampledUtc\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\","
        "\"pid\":%lu,\"type\":\"%s\",\"nid\":%d,\"otherNid\":%d,\"currentFamily\":%d,"
        "\"previousDaily\":%d,\"daily\":%d,\"previousLifetime\":%d,\"lifetime\":%d,"
        "\"previousStateBitsRaw\":%d,\"stateBitsRaw\":%d,\"lostKnownFlagBits\":%u}\n",
        utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond,
        (unsigned long)GetCurrentProcessId(), kind, after->viewer, after->other,
        (int)previous.current_family, before->daily, after->values[0],
        before->lifetime, after->values[2], before->state_bits, after->values[1], lost_bits);
    if (length <= 0 || length >= (int)sizeof(json)) return;
    file = CreateFileA(events_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                       NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, json, (DWORD)length, &written, NULL);
    CloseHandle(file);
}

static void track_relation(const RelationRaw *raw, TrackedRelation *next)
{
    const TrackedRelation *old = NULL;
    unsigned lost;
    for (unsigned i = 0; i < MAX_TRACKED_RELATIONS; ++i)
        if (tracked_relations[i].valid &&
            tracked_relations[i].family == previous.current_family &&
            tracked_relations[i].viewer == raw->viewer &&
            tracked_relations[i].other == raw->other) old = &tracked_relations[i];
    if (old) {
        if (old->daily - raw->values[0] >= 20)
            emit_relation_change("relationship_daily_drop", old, raw, 0);
        if (raw->values[0] - old->daily >= 5)
            emit_relation_change("relationship_daily_rise", old, raw, 0);
        lost = ((unsigned)old->state_bits & ~(unsigned)raw->values[1]) & 0x33u;
        if (lost)
            emit_relation_change("relationship_flag_removed", old, raw, lost);
    }
    *next = (TrackedRelation){raw->viewer, raw->other, previous.current_family,
                              raw->values[0], raw->values[2], raw->values[1], TRUE};
}

// Mirrors only the read branch of the registered GetNeighborRelationship
// wrapper at 0x008FD080. Its optional fourth argument can extend a row;
// this probe does not call that branch or the registered setter at 0x008FD250.
// Keep outputs unnamed until checked against both directional UI panels.
static BOOL read_relation_direction(void *neighborhood, int viewer, int other,
                                    RelationRaw *out)
{
    void **vt, *neighbor, *matrix;
    GetNeighbor lookup;
    GetRelationMatrix get_matrix;
    GetRelationCount get_count;
    GetRelationValue get_value;
    unsigned count;
    if (!readable(neighborhood, sizeof(void *))) return FALSE;
    vt = *(void ***)neighborhood;
    if (!readable(vt, 0x44)) return FALSE;
    lookup = (GetNeighbor)vt[16];
    if (!executable((const void *)lookup)) return FALSE;
    neighbor = lookup(neighborhood, viewer, 1);
    if (!readable(neighbor, sizeof(void *))) return FALSE;
    vt = *(void ***)neighbor;
    if (!readable(vt, 0x34)) return FALSE;
    get_matrix = (GetRelationMatrix)vt[12];
    if (!executable((const void *)get_matrix)) return FALSE;
    matrix = get_matrix(neighbor);
    if (!readable(matrix, sizeof(void *))) return FALSE;
    vt = *(void ***)matrix;
    if (!readable(vt, 0x28)) return FALSE;
    get_count = (GetRelationCount)vt[5];
    get_value = (GetRelationValue)vt[9];
    if (!executable((const void *)get_count) || !executable((const void *)get_value)) return FALSE;
    count = get_count(matrix, other);
    if (count < 3 || count > 64) return FALSE;
    out->viewer = viewer;
    out->other = other;
    out->count = count;
    for (unsigned i = 0; i < 10; ++i)
        out->values[i] = i < count ? get_value(matrix, other, (int)i) : 0;
    if (out->values[0] < -100 || out->values[0] > 100 ||
        out->values[2] < -100 || out->values[2] > 100) return FALSE;
    return TRUE;
}

static void probe_relation_direction(void *neighborhood, int viewer, int other)
{
    RelationRaw raw;
    char line[256];
    size_t used;
    if (!read_relation_direction(neighborhood, viewer, other, &raw)) return;
    used = (size_t)snprintf(line, sizeof(line),
                            "RELATION_RAW viewerNID=%d otherNID=%d count=%u",
                            viewer, other, raw.count);
    for (unsigned i = 0; i < 10 && i < raw.count && used < sizeof(line); ++i) {
        int n = snprintf(line + used, sizeof(line) - used, " index%u=%d",
                         i, raw.values[i]);
        if (n <= 0 || (size_t)n >= sizeof(line) - used) return;
        used += (size_t)n;
    }
    log_line(line);
}

// A target must be explicitly chosen by the operator for the current hood.
// Parse once at startup; never guess a visitor from a loaded object alone.
static int load_relationship_target(void)
{
    char data[32];
    HANDLE file = CreateFileA(relationship_target_path, GENERIC_READ, FILE_SHARE_READ,
                              NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD length = 0;
    unsigned value = 0, i = 0;
    if (file == INVALID_HANDLE_VALUE) return 0;
    BOOL okay = ReadFile(file, data, sizeof(data) - 1, &length, NULL);
    CloseHandle(file);
    if (!okay || !length || length >= sizeof(data) - 1) return 0;
    data[length] = '\0';
    while (data[i] == ' ' || data[i] == '\t') ++i;
    if (data[i] < '0' || data[i] > '9') return 0;
    while (data[i] >= '0' && data[i] <= '9') {
        value = value * 10 + (unsigned)(data[i++] - '0');
        if (value > 32767) return 0;
    }
    while (data[i] == ' ' || data[i] == '\t' || data[i] == '\r' || data[i] == '\n') ++i;
    return data[i] == '\0' && value > 0 ? (int)value : 0;
}

static BOOL append_relation_pair(char *json, size_t cap, size_t *used, const RelationRaw *raw)
{
    int n = snprintf(json + *used, cap - *used,
        "%s{\"viewerNid\":%d,\"otherNid\":%d,\"daily\":%d,\"lifetime\":%d,"
        "\"stateBitsRaw\":%d,\"index3Raw\":%d,\"rawSlots\":[",
        json[*used - 1] == '[' ? "" : ",", raw->viewer, raw->other,
        raw->values[0], raw->values[2], raw->values[1], raw->values[3]);
    if (n < 0 || (size_t)n >= cap - *used) return FALSE;
    *used += (size_t)n;
    for (unsigned i = 0; i < raw->count && i < 10; ++i) {
        n = snprintf(json + *used, cap - *used, "%s%d", i ? "," : "", raw->values[i]);
        if (n < 0 || (size_t)n >= cap - *used) return FALSE;
        *used += (size_t)n;
    }
    n = snprintf(json + *used, cap - *used, "]}");
    if (n < 0 || (size_t)n >= cap - *used) return FALSE;
    *used += (size_t)n;
    return TRUE;
}

static void write_relationship_state(const RosterSim *sims, unsigned count, BOOL available)
{
    char json[32768];
    size_t used;
    unsigned members[MAX_HOUSEHOLD_RELATIONS], member_count = 0, target_index = count;
    void *root = NULL, *neighborhood = NULL;
    void **vt;
    GetNeighborhood get_neighborhood;
    SYSTEMTIME utc;
    HANDLE file;
    DWORD written;
    BOOL okay;
    TrackedRelation next_relations[MAX_TRACKED_RELATIONS] = {{0}};
    unsigned next_count = 0;
    GetSystemTime(&utc);
    used = (size_t)snprintf(json, sizeof(json),
        "{\"schema\":1,\"bridge\":\"1.33\",\"sampledUtc\":\"%04u-%02u-%02uT%02u:%02u:%02uZ\",\"status\":\"%s\",\"currentFamily\":%d,\"configuredTargetNid\":%d,\"pairs\":[",
        utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond,
        available ? "lot" : "unavailable", available ? (int)previous.current_family : 0,
        relationship_target_nid);
    if (used >= sizeof(json)) return;
    if (available) {
        for (unsigned i = 0; i < count; ++i)
            if (sims[i].family_number == previous.current_family &&
                sims[i].nid > 0 && member_count < MAX_HOUSEHOLD_RELATIONS)
                members[member_count++] = i;
        if (relationship_target_nid && previous.current_family > 0)
            for (unsigned i = 0; i < count; ++i)
                if (sims[i].nid == relationship_target_nid &&
                    sims[i].family_number > 0 && sims[i].family_number != 32767 &&
                    sims[i].family_number != previous.current_family)
                    target_index = i;
        if (member_count >= 2 || (member_count && target_index < count)) {
            root = ((GetRoot)((uintptr_t)game_module + ROOT_RVA))();
            if (readable(root, sizeof(void *))) {
                vt = *(void ***)root;
                if (readable(vt, 0x2c)) {
                    get_neighborhood = (GetNeighborhood)vt[10];
                    if (executable((const void *)get_neighborhood))
                        neighborhood = get_neighborhood(root);
                }
            }
        }
        for (unsigned i = 0; i < member_count && neighborhood; ++i) {
            for (unsigned j = 0; j < member_count; ++j) {
                RelationRaw raw;
                if (i == j || !read_relation_direction(neighborhood,
                        sims[members[i]].nid, sims[members[j]].nid, &raw)) continue;
                if (next_count < MAX_TRACKED_RELATIONS)
                    track_relation(&raw, &next_relations[next_count++]);
                if (!append_relation_pair(json, sizeof(json), &used, &raw)) return;
            }
        }
        if (target_index < count && neighborhood)
            for (unsigned i = 0; i < member_count; ++i) {
                RelationRaw raw;
                if (read_relation_direction(neighborhood, sims[members[i]].nid,
                                            sims[target_index].nid, &raw)) {
                    if (next_count < MAX_TRACKED_RELATIONS)
                        track_relation(&raw, &next_relations[next_count++]);
                    if (!append_relation_pair(json, sizeof(json), &used, &raw)) return;
                }
                if (read_relation_direction(neighborhood, sims[target_index].nid,
                                            sims[members[i]].nid, &raw)) {
                    if (next_count < MAX_TRACKED_RELATIONS)
                        track_relation(&raw, &next_relations[next_count++]);
                    if (!append_relation_pair(json, sizeof(json), &used, &raw)) return;
                }
            }
    }
    for (unsigned i = 0; i < MAX_TRACKED_RELATIONS; ++i)
        tracked_relations[i] = next_relations[i];
    if (used + 4 >= sizeof(json)) return;
    json[used++] = ']';
    {
        unsigned loaded_household_count = 0;
        if (available)
            for (unsigned i = 0; i < count; ++i)
                if (sims[i].family_number == previous.current_family && sims[i].nid > 0)
                    ++loaded_household_count;
        int n = snprintf(json + used, sizeof(json) - used,
                         ",\"sampledHouseholdCount\":%u,\"coverageTruncated\":%s",
                         member_count,
                         loaded_household_count > MAX_HOUSEHOLD_RELATIONS ? "true" : "false");
        if (n < 0 || (size_t)n >= sizeof(json) - used) return;
        used += (size_t)n;
    }
    json[used++] = '}';
    json[used++] = '\n';
    file = CreateFileA(relationship_temp_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    okay = WriteFile(file, json, (DWORD)used, &written, NULL) && written == used;
    if (!CloseHandle(file) || !okay) return;
    MoveFileExA(relationship_temp_path, relationship_path, MOVEFILE_REPLACE_EXISTING);
}

static void probe_relationships(const RosterSim *sims, unsigned count)
{
    const RosterSim *pair[2] = {NULL, NULL};
    void *root, *neighborhood;
    void **vt;
    GetNeighborhood get_neighborhood;
    unsigned found = 0;
    for (unsigned i = 0; i < count; ++i) {
        if (sims[i].family_number == previous.current_family &&
            sims[i].nid > 0 && found < 2)
            pair[found++] = &sims[i];
    }
    if (found < 2) {
        log_line("RELATION_PROBE requires two loaded members of the current household.");
        return;
    }
    {
        char line[120];
        snprintf(line, sizeof(line), "RELATION_PROBE pair NID=%d NID=%d (two directions)",
                 pair[0]->nid, pair[1]->nid);
        log_line(line);
    }
    root = ((GetRoot)((uintptr_t)game_module + ROOT_RVA))();
    if (!readable(root, sizeof(void *))) return;
    vt = *(void ***)root;
    if (!readable(vt, 0x2c)) return;
    get_neighborhood = (GetNeighborhood)vt[10];
    if (!executable((const void *)get_neighborhood)) return;
    neighborhood = get_neighborhood(root);
    probe_relation_direction(neighborhood, pair[0]->nid, pair[1]->nid);
    probe_relation_direction(neighborhood, pair[1]->nid, pair[0]->nid);
}

static BOOL capture(Snapshot *out)
{
    void *root, *simulator;
    void **vt;
    GetSimulator get_simulator;
    GetGlobal get_global;
    void *person;
    GetPersonData get_person_data;
    GetMotive get_motive;
    int16_t oid;

    root = ((GetRoot)((uintptr_t)game_module + ROOT_RVA))();
    if (!readable(root, sizeof(void *))) return FALSE;
    vt = *(void ***)root;
    if (!readable(vt, 0x10)) return FALSE;
    get_simulator = (GetSimulator)vt[3];
    if (!executable((const void *)get_simulator)) return FALSE;
    simulator = get_simulator(root);
    if (!readable(simulator, sizeof(void *))) return FALSE;
    vt = *(void ***)simulator;
    if (!readable(vt, 0x40)) return FALSE;
    get_global = (GetGlobal)vt[15];
    if (!executable((const void *)get_global)) return FALSE;
    oid = get_global(simulator, 3);
    if (oid <= 0) return FALSE;
    out->hour = get_global(simulator, 0);
    out->minute = get_global(simulator, 5);
    out->current_family = get_global(simulator, 9); // Lot's current family (raw probe).
    // Candidate Global indices from the documented sequence: Speed 16,
    // Paused 17, Held Sim Speed 18, Mode 19. Verify in the live UI.
    out->paused_probe_raw = get_global(simulator, 17);
    out->mode_probe_raw = get_global(simulator, 19);
    if (out->hour < 0 || out->hour > 23 || out->minute < 0 || out->minute > 59) return FALSE;
    // Mirrors the game's registered GetPersonData wrapper at 0x00900bf0:
    // resolve its OID via 0x008fe4d0, then call vtable +0x28 with (field, 1).
    person = ((LookupPerson)((uintptr_t)game_module + OBJECT_LOOKUP_RVA))((int)oid);
    if (!readable(person, sizeof(void *))) return FALSE;
    vt = *(void ***)person;
    if (!readable(vt, 0x38)) return FALSE;
    get_person_data = (GetPersonData)vt[10];
    if (!executable((const void *)get_person_data)) return FALSE;
    out->nid = get_person_data(person, 0x1f, 1);
    if (out->nid <= 0) return FALSE;
    out->family_number = get_person_data(person, 0x3d, 1); // Person data 61.
    // The game's GetPersonMotive wrapper at 0x00900d60 calls vtable +0x34
    // with a motive index; its return is a floating-point x87 value.
    get_motive = (GetMotive)vt[13];
    if (!executable((const void *)get_motive)) return FALSE;
    out->hunger = get_motive(person, 7);
    if (out->hunger < -100.0f || out->hunger > 100.0f || out->hunger != out->hunger) return FALSE;
    out->oid = oid;
    return TRUE;
}

static void process_snapshot(void)
{
    Snapshot now;
    char message[256];
    if (!capture(&now)) {
        if (have_previous) log_line("Sampling suspended; selected Sim or lot unavailable.");
        else if (!unavailable_reported) log_line("Waiting for a selected Sim on a loaded lot.");
        unavailable_reported = TRUE;
        have_previous = FALSE;
        roster_initialized = FALSE;
        tracked_count = 0;
        known_household_count = 0;
        write_state(NULL, 0, FALSE);
        write_relationship_state(NULL, 0, FALSE);
        return;
    }
    unavailable_reported = FALSE;
    if (have_previous && previous.current_family != now.current_family) {
        roster_initialized = FALSE;
        tracked_count = 0;
        known_household_count = 0;
    }
    if (!have_previous || previous.nid != now.nid || previous.oid != now.oid) {
        snprintf(message, sizeof(message), "FOCUS NID=%u OID=%u; Hunger=%.2f; family=%d currentFamily=%d; game time=%d:%02d; thread=%lu",
                 (unsigned)(uint16_t)now.nid, (unsigned)(uint16_t)now.oid,
                 (double)now.hunger, (int)now.family_number, (int)now.current_family,
                 (int)now.hour, (int)now.minute,
                 (unsigned long)GetCurrentThreadId());
        log_line(message);
    }
    previous = now;
    have_previous = TRUE;
}

static void update_roster(const RosterSim *sims, unsigned count)
{
    BOOL had_roster = roster_initialized;
    char line[480];
    DWORD tick = GetTickCount();
    // Keep household identities only for this lot session. A former member
    // can reappear later with a different family number (e.g. a ghost).
    for (unsigned i = 0; i < count; ++i)
        if (sims[i].family_number == previous.current_family &&
            !known_household_member(sims[i].nid) &&
            known_household_count < MAX_PERSONS)
            known_household_nids[known_household_count++] = sims[i].nid;
    for (unsigned i = 0; i < tracked_count; ++i) tracked[i].missed_scans++;
    for (unsigned i = 0; i < count; ++i) {
        unsigned slot = 0;
        while (slot < tracked_count && tracked[slot].last.nid != sims[i].nid) ++slot;
        if (slot == tracked_count) {
            if (tracked_count == MAX_PERSONS) continue;
            tracked[slot].last = sims[i];
            tracked[slot].warning_active = sims[i].hunger < HUNGER_WARNING;
            tracked[slot].urgent_active = sims[i].hunger < HUNGER_URGENT;
            tracked[slot].bladder_warning_active =
                (sims[i].extra_motives_valid & (1u << BLADDER_MOTIVE_SLOT)) &&
                sims[i].extra_motives[BLADDER_MOTIVE_SLOT] < BLADDER_WARNING;
            tracked[slot].bladder_urgent_active =
                (sims[i].extra_motives_valid & (1u << BLADDER_MOTIVE_SLOT)) &&
                sims[i].extra_motives[BLADDER_MOTIVE_SLOT] < BLADDER_URGENT;
            tracked[slot].energy_warning_active =
                (sims[i].extra_motives_valid & (1u << ENERGY_MOTIVE_SLOT)) &&
                sims[i].extra_motives[ENERGY_MOTIVE_SLOT] < ENERGY_WARNING;
            tracked[slot].energy_urgent_active =
                (sims[i].extra_motives_valid & (1u << ENERGY_MOTIVE_SLOT)) &&
                sims[i].extra_motives[ENERGY_MOTIVE_SLOT] < ENERGY_URGENT;
            tracked[slot].missed_scans = 0;
            ++tracked_count;
            if (had_roster) {
                snprintf(line, sizeof(line), "LOT_SIM_APPEARED NID=%u OID=%u",
                         (unsigned)(uint16_t)sims[i].nid, (unsigned)(uint16_t)sims[i].oid);
                log_line(line);
                emit_event("loaded_person_appeared", sims[i], count, NULL);
                if (sims[i].new_household_identity && sims[i].age_raw == 0x01) {
                    snprintf(line, sizeof(line), "NEW_HOUSEHOLD_BABY_OBSERVED NID=%u",
                             (unsigned)(uint16_t)sims[i].nid);
                    log_line(line);
                    emit_event("new_household_baby_observed", sims[i], count, NULL);
                }
                if (sims[i].school_grade_raw != CAREER_UNAVAILABLE)
                    emit_event("person_progress_baseline", sims[i], count, NULL);
            }
            continue;
        }
        if (tracked[slot].missed_scans == 0) continue; // Duplicate NID within one scan.
        tracked[slot].missed_scans = 0;
        if (sims[i].family_number != tracked[slot].last.family_number &&
            (sims[i].family_number == previous.current_family ||
             tracked[slot].last.family_number == previous.current_family)) {
            snprintf(line, sizeof(line), "FAMILY_NUMBER_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.family_number, sims[i].family_number);
            log_line(line);
            emit_event("family_number_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].ghost_flags_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.ghost_flags_raw != CAREER_UNAVAILABLE &&
            sims[i].ghost_flags_raw != tracked[slot].last.ghost_flags_raw) {
            snprintf(line, sizeof(line), "GHOST_FLAGS_RAW_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.ghost_flags_raw, sims[i].ghost_flags_raw);
            log_line(line);
            emit_event("ghost_flags_raw_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].family_number == previous.current_family &&
            tracked[slot].last.family_number == previous.current_family &&
            sims[i].body_flags_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.body_flags_raw != CAREER_UNAVAILABLE &&
            (((uint16_t)sims[i].body_flags_raw ^
              (uint16_t)tracked[slot].last.body_flags_raw) & 0x001bu) != 0) {
            snprintf(line, sizeof(line), "BODY_FLAGS_RAW_CHANGED NID=%u value=%u->%u",
                     (unsigned)(uint16_t)sims[i].nid,
                     (unsigned)(uint16_t)tracked[slot].last.body_flags_raw,
                     (unsigned)(uint16_t)sims[i].body_flags_raw);
            log_line(line);
            emit_event("body_flags_raw_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].family_number == previous.current_family &&
            tracked[slot].last.family_number == previous.current_family &&
            sims[i].age_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.age_raw != CAREER_UNAVAILABLE &&
            sims[i].age_raw != tracked[slot].last.age_raw) {
            snprintf(line, sizeof(line), "AGE_RAW_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.age_raw, sims[i].age_raw);
            log_line(line);
            emit_event("age_raw_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].family_number == previous.current_family &&
            tracked[slot].last.family_number == previous.current_family &&
            sims[i].aspiration_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.aspiration_raw != CAREER_UNAVAILABLE &&
            sims[i].aspiration_raw != tracked[slot].last.aspiration_raw) {
            snprintf(line, sizeof(line), "ASPIRATION_RAW_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.aspiration_raw, sims[i].aspiration_raw);
            log_line(line);
            emit_event("aspiration_raw_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].family_number == previous.current_family &&
            tracked[slot].last.family_number == previous.current_family &&
            sims[i].lta_raw[2] != CAREER_UNAVAILABLE &&
            tracked[slot].last.lta_raw[2] != CAREER_UNAVAILABLE &&
            sims[i].lta_raw[2] != tracked[slot].last.lta_raw[2]) {
            snprintf(line, sizeof(line), "LIFETIME_BENEFIT_SPENT_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.lta_raw[2], sims[i].lta_raw[2]);
            log_line(line);
            emit_event("lifetime_benefit_spent_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].school_grade_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.school_grade_raw != CAREER_UNAVAILABLE) {
            if (sims[i].school_grade_raw != tracked[slot].last.school_grade_raw) {
                BOOL is_student = sims[i].age_raw == 0x03 || sims[i].age_raw == 0x10;
                snprintf(line, sizeof(line), "SCHOOL_GRADE_RAW_CHANGED NID=%u value=%d->%d age=%d",
                         (unsigned)(uint16_t)sims[i].nid,
                         tracked[slot].last.school_grade_raw, sims[i].school_grade_raw,
                         sims[i].age_raw);
                log_line(line);
                emit_event(is_student ? "school_grade_changed" : "school_grade_raw_changed",
                           sims[i], count, &tracked[slot].last);
            }
            if (sims[i].job_level_raw != tracked[slot].last.job_level_raw) {
                BOOL can_have_job = sims[i].age_raw == 0x10 ||
                    sims[i].age_raw == 0x13 || sims[i].age_raw == 0x33;
                BOOL level_increased = can_have_job &&
                    sims[i].job_level_raw == tracked[slot].last.job_level_raw + 1;
                snprintf(line, sizeof(line), "JOB_LEVEL_RAW_CHANGED NID=%u level=%d->%d age=%d",
                         (unsigned)(uint16_t)sims[i].nid,
                         tracked[slot].last.job_level_raw, sims[i].job_level_raw,
                         sims[i].age_raw);
                log_line(line);
                emit_event(level_increased ? "job_level_increased" : "job_level_raw_changed",
                           sims[i], count, &tracked[slot].last);
            }
        }
        if (sims[i].school_grade_raw != CAREER_UNAVAILABLE &&
            tracked[slot].last.school_grade_raw != CAREER_UNAVAILABLE &&
            ((sims[i].job_performance_raw == 0 && tracked[slot].last.job_performance_raw != 0) ||
             (sims[i].job_performance_raw != 0 && tracked[slot].last.job_performance_raw == 0))) {
            snprintf(line, sizeof(line), "JOB_PERFORMANCE_CHANGED NID=%u value=%d->%d",
                     (unsigned)(uint16_t)sims[i].nid,
                     tracked[slot].last.job_performance_raw, sims[i].job_performance_raw);
            log_line(line);
            emit_event("job_performance_changed", sims[i], count, &tracked[slot].last);
        }
        if (sims[i].family_number == previous.current_family &&
            sims[i].ghost_flags_raw == 0) {
            if (!tracked[slot].warning_active && sims[i].hunger < HUNGER_WARNING) {
                snprintf(line, sizeof(line), "HUNGER_WARNING NID=%u; Hunger=%.2f",
                         (unsigned)(uint16_t)sims[i].nid, (double)sims[i].hunger);
                log_line(line);
                emit_event("hunger_warning", sims[i], count, NULL);
                tracked[slot].warning_active = TRUE;
            }
            if (!tracked[slot].urgent_active && sims[i].hunger < HUNGER_URGENT) {
                snprintf(line, sizeof(line), "HUNGER_URGENT NID=%u; Hunger=%.2f",
                         (unsigned)(uint16_t)sims[i].nid, (double)sims[i].hunger);
                log_line(line);
                emit_event("hunger_urgent", sims[i], count, NULL);
                tracked[slot].urgent_active = TRUE;
            }
            if (tracked[slot].urgent_active && sims[i].hunger >= URGENT_RECOVERED) {
                snprintf(line, sizeof(line), "HUNGER_URGENT_RECOVERED NID=%u; Hunger=%.2f",
                         (unsigned)(uint16_t)sims[i].nid, (double)sims[i].hunger);
                log_line(line);
                emit_event("hunger_urgent_recovered", sims[i], count, NULL);
                tracked[slot].urgent_active = FALSE;
            }
            if (tracked[slot].warning_active && sims[i].hunger >= WARNING_RECOVERED) {
                snprintf(line, sizeof(line), "HUNGER_WARNING_RECOVERED NID=%u; Hunger=%.2f",
                         (unsigned)(uint16_t)sims[i].nid, (double)sims[i].hunger);
                log_line(line);
                emit_event("hunger_warning_recovered", sims[i], count, NULL);
                tracked[slot].warning_active = FALSE;
            }
            // Establish a baseline on first valid household reading. A Sim
            // loaded as a caller or visitor has no household motive reading.
            if (sims[i].extra_motives_valid & (1u << BLADDER_MOTIVE_SLOT)) {
                float bladder = sims[i].extra_motives[BLADDER_MOTIVE_SLOT];
                if (tracked[slot].last.family_number != previous.current_family ||
                    !(tracked[slot].last.extra_motives_valid & (1u << BLADDER_MOTIVE_SLOT))) {
                    tracked[slot].bladder_warning_active = bladder < BLADDER_WARNING;
                    tracked[slot].bladder_urgent_active = bladder < BLADDER_URGENT;
                } else {
                    if (!tracked[slot].bladder_warning_active && bladder < BLADDER_WARNING) {
                        snprintf(line, sizeof(line), "BLADDER_WARNING NID=%u; Bladder=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)bladder);
                        log_line(line);
                        emit_event("bladder_warning", sims[i], count, NULL);
                        tracked[slot].bladder_warning_active = TRUE;
                    }
                    if (!tracked[slot].bladder_urgent_active && bladder < BLADDER_URGENT) {
                        snprintf(line, sizeof(line), "BLADDER_URGENT NID=%u; Bladder=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)bladder);
                        log_line(line);
                        emit_event("bladder_urgent", sims[i], count, NULL);
                        tracked[slot].bladder_urgent_active = TRUE;
                    }
                    if (tracked[slot].bladder_urgent_active &&
                        bladder >= BLADDER_URGENT_RECOVERED) {
                        snprintf(line, sizeof(line), "BLADDER_URGENT_RECOVERED NID=%u; Bladder=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)bladder);
                        log_line(line);
                        emit_event("bladder_urgent_recovered", sims[i], count, NULL);
                        tracked[slot].bladder_urgent_active = FALSE;
                    }
                    if (tracked[slot].bladder_warning_active &&
                        bladder >= BLADDER_WARNING_RECOVERED) {
                        snprintf(line, sizeof(line), "BLADDER_WARNING_RECOVERED NID=%u; Bladder=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)bladder);
                        log_line(line);
                        emit_event("bladder_warning_recovered", sims[i], count, NULL);
                        tracked[slot].bladder_warning_active = FALSE;
                    }
                }
            }
            if (sims[i].extra_motives_valid & (1u << ENERGY_MOTIVE_SLOT)) {
                float energy = sims[i].extra_motives[ENERGY_MOTIVE_SLOT];
                if (tracked[slot].last.family_number != previous.current_family ||
                    !(tracked[slot].last.extra_motives_valid & (1u << ENERGY_MOTIVE_SLOT))) {
                    tracked[slot].energy_warning_active = energy < ENERGY_WARNING;
                    tracked[slot].energy_urgent_active = energy < ENERGY_URGENT;
                } else {
                    if (!tracked[slot].energy_warning_active && energy < ENERGY_WARNING) {
                        snprintf(line, sizeof(line), "ENERGY_WARNING NID=%u; Energy=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)energy);
                        log_line(line);
                        emit_event("energy_warning", sims[i], count, NULL);
                        tracked[slot].energy_warning_active = TRUE;
                    }
                    if (!tracked[slot].energy_urgent_active && energy < ENERGY_URGENT) {
                        snprintf(line, sizeof(line), "ENERGY_URGENT NID=%u; Energy=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)energy);
                        log_line(line);
                        emit_event("energy_urgent", sims[i], count, NULL);
                        tracked[slot].energy_urgent_active = TRUE;
                    }
                    if (tracked[slot].energy_urgent_active &&
                        energy >= ENERGY_URGENT_RECOVERED) {
                        snprintf(line, sizeof(line), "ENERGY_URGENT_RECOVERED NID=%u; Energy=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)energy);
                        log_line(line);
                        emit_event("energy_urgent_recovered", sims[i], count, NULL);
                        tracked[slot].energy_urgent_active = FALSE;
                    }
                    if (tracked[slot].energy_warning_active &&
                        energy >= ENERGY_WARNING_RECOVERED) {
                        snprintf(line, sizeof(line), "ENERGY_WARNING_RECOVERED NID=%u; Energy=%.2f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)energy);
                        log_line(line);
                        emit_event("energy_warning_recovered", sims[i], count, NULL);
                        tracked[slot].energy_warning_active = FALSE;
                    }
                }
            }
        }
        tracked[slot].last = sims[i];
    }
    for (unsigned i = 0; i < tracked_count;) {
        if (tracked[i].missed_scans < 2) { ++i; continue; }
        snprintf(line, sizeof(line), "LOT_SIM_DISAPPEARED NID=%u OID=%u",
                 (unsigned)(uint16_t)tracked[i].last.nid,
                 (unsigned)(uint16_t)tracked[i].last.oid);
        log_line(line);
        emit_event("loaded_person_disappeared", tracked[i].last, count, NULL);
        tracked[i] = tracked[--tracked_count];
    }
    if (!roster_initialized && count) {
        snprintf(line, sizeof(line), "LOT_BASELINE loaded Sims=%u; selected NID=%u",
                 count, (unsigned)(uint16_t)previous.nid);
        log_line(line);
        RosterSim selected = {.oid = previous.oid, .nid = previous.nid,
                              .family_number = previous.family_number, .hunger = previous.hunger,
                              .school_grade_raw = CAREER_UNAVAILABLE,
                              .job_level_raw = CAREER_UNAVAILABLE,
                              .job_performance_raw = CAREER_UNAVAILABLE,
                              .age_raw = CAREER_UNAVAILABLE,
                              .gender_raw = CAREER_UNAVAILABLE,
                              .ghost_flags_raw = CAREER_UNAVAILABLE};
        for (unsigned i = 0; i < count; ++i) {
            if (sims[i].nid == previous.nid) selected = sims[i];
        }
        emit_event("lot_baseline", selected, count, NULL);
        for (unsigned i = 0; i < count; ++i)
            if (sims[i].school_grade_raw != CAREER_UNAVAILABLE)
                emit_event("person_progress_baseline", sims[i], count, NULL);
        snprintf(line, sizeof(line), "FAMILY_PROBE currentFamily=%d selectedFamily=%d; loaded=%u",
                 (int)previous.current_family, (int)previous.family_number, count);
        log_line(line);
        roster_initialized = TRUE;
        last_roster_summary = tick;
    } else if (roster_initialized && (DWORD)(tick - last_roster_summary) >= 60000u) {
        size_t used = (size_t)snprintf(line, sizeof(line), "LOT_SUMMARY Sims=%u", count);
        for (unsigned i = 0; i < count && i < 16 && used < sizeof(line) - 35; ++i) {
            int wrote = snprintf(line + used, sizeof(line) - used, " NID%u:%.1f",
                                 (unsigned)(uint16_t)sims[i].nid, (double)sims[i].hunger);
            if (wrote <= 0 || (size_t)wrote >= sizeof(line) - used) break;
            used += (size_t)wrote;
        }
        log_line(line);
        last_roster_summary = tick;
    }
    write_state(sims, count, TRUE);
}

// Mirrors the registered getPersonIds implementation at 0x00904690.
// Its one-shot paused-lot test succeeded; use the same path for local samples.
static void scan_roster(BOOL verbose, BOOL relationship_requested)
{
    void *root, *manager, *vector, *object, *candidate, *person;
    void **vt, **begin, **end;
    uintptr_t span;
    unsigned entries, found = 0, printed = 0, count = 0;
    RosterSim sims[MAX_PERSONS];
    char line[256];
    GetObjectManager get_manager;
    GetObjectVector get_vector;
    LookupPerson lookup = (LookupPerson)((uintptr_t)game_module + OBJECT_LOOKUP_RVA);
    root = ((GetRoot)((uintptr_t)game_module + ROOT_RVA))();
    if (!readable(root, sizeof(void *))) goto unavailable;
    vt = *(void ***)root;
    if (!readable(vt, 0x6c)) goto unavailable;
    get_manager = (GetObjectManager)vt[26]; // +0x68
    if (!executable((const void *)get_manager)) goto unavailable;
    manager = get_manager(root);
    if (!readable(manager, sizeof(void *))) goto unavailable;
    vt = *(void ***)manager;
    if (!readable(vt, 0xb8)) goto unavailable;
    get_vector = (GetObjectVector)vt[45]; // +0xb4
    if (!executable((const void *)get_vector)) goto unavailable;
    vector = get_vector(manager);
    if (!readable(vector, 2 * sizeof(void *))) goto unavailable;
    begin = *(void ***)vector;
    end = *((void ***)vector + 1);
    if ((uintptr_t)end < (uintptr_t)begin) goto unavailable;
    span = (uintptr_t)end - (uintptr_t)begin;
    if (span % sizeof(void *) || span > 8192u * sizeof(void *)) goto unavailable;
    entries = (unsigned)(span / sizeof(void *));
    if (verbose) {
        snprintf(line, sizeof(line), "ROSTER_PROBE begin; loaded object slots=%u; GUI thread=%lu",
                 entries, (unsigned long)GetCurrentThreadId());
        log_line(line);
    }
    for (unsigned index = 0; index < entries; ++index) {
        void **slot = begin + index;
        void **object_vt, **candidate_vt, **person_vt;
        GetPersonInterface get_candidate;
        HasObjectFlag has_flag;
        GetObjectField get_field;
        GetObjectID get_oid;
        GetPersonData get_data;
        GetMotive get_motive;
        int16_t oid, nid, family_number;
        float hunger;
        if (!readable(slot, sizeof(void *))) break;
        object = *slot;
        if (!readable(object, sizeof(void *))) continue;
        object_vt = *(void ***)object;
        if (!readable(object_vt, 0x24)) continue;
        get_candidate = (GetPersonInterface)object_vt[8]; // +0x20
        if (!executable((const void *)get_candidate)) continue;
        candidate = get_candidate(object);
        if (!readable(candidate, sizeof(void *))) continue;
        candidate_vt = *(void ***)candidate;
        if (!readable(candidate_vt, 0xc0)) continue;
        has_flag = (HasObjectFlag)candidate_vt[25]; // +0x64
        get_field = (GetObjectField)candidate_vt[13]; // +0x34
        get_oid = (GetObjectID)candidate_vt[47]; // +0xbc
        if (!executable((const void *)has_flag) || !executable((const void *)get_field) ||
            !executable((const void *)get_oid)) continue;
        if (has_flag(candidate, 0x10000u) || has_flag(candidate, 0x20000u)) continue;
        if (get_field(candidate, 0x38) & 0x5) continue;
        oid = get_oid(candidate);
        if (oid <= 0) continue;
        ++found;
        if (count >= MAX_PERSONS) continue;
        person = lookup(oid);
        if (!readable(person, sizeof(void *))) continue;
        person_vt = *(void ***)person;
        if (!readable(person_vt, 0x38)) continue;
        get_data = (GetPersonData)person_vt[10];
        get_motive = (GetMotive)person_vt[13];
        if (!executable((const void *)get_data) || !executable((const void *)get_motive)) continue;
        nid = get_data(person, 0x1f, 1);
        family_number = get_data(person, 0x3d, 1);
        hunger = get_motive(person, 7);
        if (nid <= 0 || hunger != hunger || hunger < -100.0f || hunger > 100.0f) continue;
        sims[count].oid = oid;
        sims[count].nid = nid;
        sims[count].family_number = family_number;
        sims[count].hunger = hunger;
        // Person data field 56 is school grade, 57 is job level, 58 is
        // age, and 63 is job performance. Read only household members.
        sims[count].school_grade_raw = CAREER_UNAVAILABLE;
        sims[count].job_level_raw = CAREER_UNAVAILABLE;
        sims[count].job_performance_raw = CAREER_UNAVAILABLE;
        sims[count].age_raw = CAREER_UNAVAILABLE;
        sims[count].gender_raw = CAREER_UNAVAILABLE;
        sims[count].preference_male_raw = CAREER_UNAVAILABLE;
        sims[count].preference_female_raw = CAREER_UNAVAILABLE;
        sims[count].ghost_flags_raw = CAREER_UNAVAILABLE;
        sims[count].body_flags_raw = CAREER_UNAVAILABLE;
        for (unsigned skill = 0; skill < 7; ++skill)
            sims[count].skills_raw[skill] = CAREER_UNAVAILABLE;
        for (unsigned hobby = 0; hobby < 10; ++hobby)
            sims[count].hobby_enthusiasm_probe_raw[hobby] = CAREER_UNAVAILABLE;
        for (unsigned interest = 0; interest < 18; ++interest)
            sims[count].interest_probe_raw[interest] = CAREER_UNAVAILABLE;
        sims[count].predestined_hobby_probe_raw = CAREER_UNAVAILABLE;
        for (unsigned trait = 0; trait < 5; ++trait)
            sims[count].personality_raw[trait] = CAREER_UNAVAILABLE;
        sims[count].aspiration_raw = CAREER_UNAVAILABLE;
        for (unsigned field = 0; field < 3; ++field)
            sims[count].lta_raw[field] = CAREER_UNAVAILABLE;
        sims[count].extra_motives_valid = 0;
        sims[count].was_household_member =
            (previous.current_family > 0 && family_number == previous.current_family) ||
            known_household_member(nid);
        sims[count].new_household_identity =
            previous.current_family > 0 && family_number == previous.current_family &&
            !known_household_member(nid);
        if (previous.current_family > 0 && family_number == previous.current_family) {
            sims[count].school_grade_raw = get_data(person, 0x38, 1);
            sims[count].job_level_raw = get_data(person, 0x39, 1);
            sims[count].age_raw = get_data(person, 0x3a, 1);
            // Candidate person-data index 65. Check against the Sim's
            // character package before interpreting its values as gender.
            sims[count].gender_raw = get_data(person, 0x41, 1);
            // SDSC Body Flags is the 16-bit word at offset 0xAE. The
            // corresponding live person-data index is 0xAE / 2 - 6 = 81.
            // Keep the full raw word; the API labels only tested bits.
            sims[count].body_flags_raw = get_data(person, 81, 1);
            sims[count].job_performance_raw = get_data(person, 0x3f, 1);
            for (unsigned skill = 0; skill < 7; ++skill)
                sims[count].skills_raw[skill] =
                    get_data(person, skill_person_data_ids[skill], 1);
            for (unsigned hobby = 0; hobby < 10; ++hobby)
                sims[count].hobby_enthusiasm_probe_raw[hobby] =
                    get_data(person, 0xCC + (int)hobby, 1);
            for (unsigned interest = 0; interest < 18; ++interest)
                sims[count].interest_probe_raw[interest] =
                    get_data(person, 0x7C + (int)interest, 1);
            sims[count].predestined_hobby_probe_raw = get_data(person, 0xD7, 1);
            for (unsigned trait = 0; trait < 5; ++trait)
                sims[count].personality_raw[trait] =
                    get_data(person, personality_person_data_ids[trait], 1);
            sims[count].aspiration_raw = get_data(person, 46, 1);
            // Candidate FreeTime SDSC offsets 0x1BC, 0x1BE, 0x1C0;
            // index = offset / 2 - 6. Expose raw until live validated.
            for (unsigned field = 0; field < 3; ++field)
                sims[count].lta_raw[field] = get_data(person, 216 + (int)field, 1);
            // SimAntics documents these indices; validate their values in
            // this executable before treating them as actionable motives.
            for (unsigned m = 0; m < 7; ++m) {
                float value = get_motive(person, extra_motive_ids[m]);
                if (value == value && value >= -100.0f && value <= 100.0f) {
                    sims[count].extra_motives[m] = value;
                    sims[count].extra_motives_valid |= (uint8_t)(1u << m);
                }
            }
        }
        // SDSC offsets 0x38/0x3A map to game person-data indices 22/23
        // (offset / 2 - 6), as with the validated skills and personality.
        // Restrict extra reads to the household or configured ordinary visitor.
        if (previous.current_family > 0 &&
            (family_number == previous.current_family ||
             (relationship_target_nid == nid && family_number > 0 &&
              family_number != 32767))) {
            sims[count].preference_male_raw = get_data(person, 22, 1);
            sims[count].preference_female_raw = get_data(person, 23, 1);
        }
        // Field 68 is documented as ghost routing flags, not as a proven
        // death marker. Never make this extra call for unrelated NPCs.
        if (sims[count].was_household_member)
            sims[count].ghost_flags_raw = get_data(person, 0x44, 1);
        ++count;
        if (verbose && printed < 16) {
            snprintf(line, sizeof(line), "ROSTER_SIM OID=%u NID=%u Hunger=%.2f family=%d%s",
                     (unsigned)(uint16_t)oid, (unsigned)(uint16_t)nid, (double)hunger,
                     (int)family_number,
                     have_previous && oid == previous.oid ? " [selected]" : "");
            log_line(line);
            ++printed;
        }
    }
    if (verbose) {
        snprintf(line, sizeof(line), "ROSTER_PROBE end; eligible person objects=%u; listed=%u", found, printed);
        log_line(line);
    }
    if (relationship_requested) probe_relationships(sims, count);
    update_roster(sims, count);
    write_relationship_state(sims, count, TRUE);
    return;
unavailable:
    if (verbose) log_line("ROSTER_PROBE unavailable: object manager/vector not ready or failed bounds checks.");
    write_state(NULL, 0, FALSE);
    write_relationship_state(NULL, 0, FALSE);
}

static LRESULT CALLBACK sample_hook(int code, WPARAM removal, LPARAM payload)
{
    if (code == HC_ACTION && removal == PM_REMOVE && payload) {
        MSG *message = (MSG *)payload;
        if (message->message == sample_message && message->hwnd == game_window) {
            BOOL roster_requested = message->wParam == 1;
            BOOL relationship_requested = message->wParam == 2;
            message->message = WM_NULL; // Keep our private request out of the game window procedure.
            InterlockedExchange(&pending, 0);
            if (!hook_seen) {
                char line[128];
                snprintf(line, sizeof(line), "Sampler callback active on window thread=%lu.",
                         (unsigned long)GetCurrentThreadId());
                log_line(line);
                hook_seen = TRUE;
            }
            process_snapshot();
            if (have_previous && previous.current_family > 0)
                scan_roster(roster_requested, relationship_requested);
            else if (have_previous) {
                write_state(NULL, 0, FALSE);
                write_relationship_state(NULL, 0, FALSE);
            }
        }
    }
    return CallNextHookEx(message_hook, code, removal, payload);
}

static BOOL CALLBACK find_game_window(HWND candidate, LPARAM result)
{
    DWORD pid = 0;
    DWORD thread_id = GetWindowThreadProcessId(candidate, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(candidate) &&
        GetWindow(candidate, GW_OWNER) == NULL) {
        *(HWND *)result = candidate;
        game_thread = thread_id;
        return FALSE;
    }
    return TRUE;
}

static DWORD WINAPI worker(LPVOID ignored)
{
    char executable_path[MAX_PATH], message[256];
    char *last_slash;
    DWORD last_post = 0, stalled_reported = 0;
    BOOL previously_pressed = FALSE, relationship_previously_pressed = FALSE;
    (void)ignored;
    game_module = GetModuleHandleA(NULL);
    if (!GetModuleFileNameA(game_module, executable_path, sizeof(executable_path))) return 0;
    last_slash = strrchr(executable_path, '\\');
    if (!last_slash || (size_t)(last_slash - executable_path) + sizeof("mods\\TS2Bridge-state.json.tmp") >= sizeof(state_temp_path) ||
        (size_t)(last_slash - executable_path) + sizeof("mods\\TS2Bridge-events.jsonl") >= sizeof(events_path) ||
        (size_t)(last_slash - executable_path) + sizeof("mods\\TS2Bridge-relationships.json.tmp") >= sizeof(relationship_temp_path)) return 0;
    last_slash[1] = '\0';
    snprintf(log_path, sizeof(log_path), "%smods\\TS2Bridge.log", executable_path);
    snprintf(state_path, sizeof(state_path), "%smods\\TS2Bridge-state.json", executable_path);
    snprintf(state_temp_path, sizeof(state_temp_path), "%smods\\TS2Bridge-state.json.tmp", executable_path);
    snprintf(events_path, sizeof(events_path), "%smods\\TS2Bridge-events.jsonl", executable_path);
    snprintf(relationship_path, sizeof(relationship_path), "%smods\\TS2Bridge-relationships.json", executable_path);
    snprintf(relationship_temp_path, sizeof(relationship_temp_path), "%smods\\TS2Bridge-relationships.json.tmp", executable_path);
    snprintf(relationship_target_path, sizeof(relationship_target_path), "%smods\\TS2Bridge-relationship-target.txt", executable_path);
    relationship_target_nid = load_relationship_target();
    snprintf(message, sizeof(message), "TS2Bridge v1.33 pause probe loaded; PID=%lu; configured target NID=%d",
             (unsigned long)GetCurrentProcessId(), relationship_target_nid);
    log_line(message);
    if (!fingerprint(game_module)) {
        log_line("Executable fingerprint mismatch. No native game functions will be called.");
        return 0;
    }
    sample_message = RegisterWindowMessageA("TS2Bridge-v1.33-pause-probe-827C6836");
    if (!sample_message) {
        log_line("Sampler unavailable: could not register message.");
        return 0;
    }
    while (!InterlockedCompareExchange(&stopping, 0, 0)) {
        EnumWindows(find_game_window, (LPARAM)&game_window);
        if (game_window) break;
        Sleep(1000);
    }
    if (!game_window || !game_thread) return 0;
    message_hook = SetWindowsHookExA(WH_GETMESSAGE, sample_hook, plugin_module, game_thread);
    if (!message_hook) {
        snprintf(message, sizeof(message), "Sampler unavailable: hook failed (Windows error %lu).",
                 (unsigned long)GetLastError());
        log_line(message);
        return 0;
    }
    snprintf(message, sizeof(message), "Sampler armed for game window thread=%lu; interval=5s; hunger warning<0 urgent<-30; bladder warning<-20 urgent<-40; energy warning<0 urgent<-40.",
             (unsigned long)game_thread);
    log_line(message);
    while (!InterlockedCompareExchange(&stopping, 0, 0)) {
        DWORD tick = GetTickCount();
        BOOL pressed = (GetAsyncKeyState(VK_F11) & 0x8000) &&
                       (GetAsyncKeyState(VK_CONTROL) & 0x8000) &&
                       (GetAsyncKeyState(VK_SHIFT) & 0x8000);
        BOOL relationship_pressed = (GetAsyncKeyState(VK_F9) & 0x8000) &&
                                    (GetAsyncKeyState(VK_CONTROL) & 0x8000) &&
                                    (GetAsyncKeyState(VK_SHIFT) & 0x8000);
        if (!IsWindow(game_window)) break;
        if (pressed && !previously_pressed)
            PostMessageA(game_window, sample_message, 1, 0);
        previously_pressed = pressed;
        if (relationship_pressed && !relationship_previously_pressed)
            PostMessageA(game_window, sample_message, 2, 0);
        relationship_previously_pressed = relationship_pressed;
        if (!InterlockedCompareExchange(&pending, 0, 0) &&
            (DWORD)(tick - last_post) >= POLL_MS) {
            InterlockedExchange(&pending, 1);
            last_post = tick;
            stalled_reported = 0;
            if (!PostMessageA(game_window, sample_message, 0, 0)) {
                InterlockedExchange(&pending, 0);
                log_line("Sampler stopped: could not post to game window.");
                break;
            }
        } else if (InterlockedCompareExchange(&pending, 0, 0) &&
                   !stalled_reported && (DWORD)(tick - last_post) >= 60000u) {
            log_line("Sampler waiting: game window has not processed a sample request.");
            stalled_reported = 1;
        }
        Sleep(60);
    }
    UnhookWindowsHookEx(message_hook);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH) {
        HANDLE thread;
        plugin_module = instance;
        DisableThreadLibraryCalls(instance);
        thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        if (thread) CloseHandle(thread);
    } else if (reason == DLL_PROCESS_DETACH && !reserved) {
        InterlockedExchange(&stopping, 1);
    }
    return TRUE;
}
