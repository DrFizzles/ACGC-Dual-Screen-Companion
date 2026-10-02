"""Independent re-computation of the struct offsets used in ac_memory_map.json.

The decomp structs (MWCC, PowerPC EABI, -align powerpc -enum int) use natural
alignment: u16 -> 2, u32/f32/int/pointer -> 4, u64 -> 8. Python's ctypes on a
64-bit host uses the same natural alignment for these fixed-width types, so a
ctypes mirror of each struct gives the same member offsets.

Modelling notes (all size/alignment-preserving):
  * pointers are modelled as c_uint32 (GameCube pointers are 32-bit).
  * mQst_base_c is "u32 bitfields (16 bits used) + lbRTC_time_c". MWCC packs
    time_limit at +2 inside the bitfield unit; ctypes puts it at +4. Both give
    sizeof == 0xC and alignment 4, which is all the enclosing structs see.
  * 1-byte bitfield groups are modelled as a single u8/s8.
  * Private_c is only modelled up to bank_account (0x122C): the next member
    (my_org) is 32-byte aligned, which ctypes cannot express.

Run:  python spec/tools/layout_check.py      (exit code 0 == all checks pass)
"""
import ctypes as C
import sys

u8, s8, u16, s16 = C.c_uint8, C.c_int8, C.c_uint16, C.c_int16
u32, s32, u64, f32 = C.c_uint32, C.c_int32, C.c_uint64, C.c_float
ptr = C.c_uint32


def S(name, fields):
    return type(name, (C.Structure,), {"_fields_": fields})


def U(name, fields):
    return type(name, (C.Union,), {"_fields_": fields})


# ---- lb_rtc.h / m_personal_id.h / m_npc_personal_id.h / m_land_h.h ----
lbRTC_time_c = S("lbRTC_time_c", [("sec", u8), ("min", u8), ("hour", u8), ("day", u8),
                                  ("weekday", u8), ("month", u8), ("year", u16)])
lbRTC_ymd_c = S("lbRTC_ymd_c", [("year", u16), ("month", u8), ("day", u8)])
PersonalID_c = S("PersonalID_c", [("player_name", u8 * 8), ("land_name", u8 * 8),
                                  ("player_id", u16), ("land_id", u16)])
AnmPersonalID_c = S("AnmPersonalID_c", [("npc_id", u16), ("land_id", u16), ("land_name", u8 * 8),
                                        ("name_id", u8), ("looks", u8)])
mLd_land_info_c = S("mLd_land_info_c", [("name", u8 * 8), ("exists", s8), ("id", u16)])

# ---- m_flashrom.h ----
mFRm_chk_t = S("mFRm_chk_t", [("version", s32), ("code", u32), ("land_id", u16),
                              ("time", lbRTC_time_c), ("checksum", u16)])

# ---- m_quest.h ----
mQst_base_c = S("mQst_base_c", [("bits", u32), ("time_limit", lbRTC_time_c)])
mQst_delivery_c = S("mQst_delivery_c", [("base", mQst_base_c), ("recipient", AnmPersonalID_c),
                                        ("sender", AnmPersonalID_c)])
mQst_errand_chain_c = S("mQst_errand_chain_c", [("used_ids", AnmPersonalID_c * 3), ("used_num", u8)])
mQst_firstjob_c = S("mQst_firstjob_c", [("used_ids", AnmPersonalID_c * 2), ("bits", u8)])
mQst_errand_info_u = U("mQst_errand_info_u", [("chain", mQst_errand_chain_c), ("first_job", mQst_firstjob_c)])
mQst_errand_c = S("mQst_errand_c", [("base", mQst_base_c), ("recipient", AnmPersonalID_c),
                                    ("sender", AnmPersonalID_c), ("item", u16), ("bits", s8),
                                    ("info", mQst_errand_info_u)])
mQst_contest_letter = S("mQst_contest_letter", [("score", u8), ("present", u16)])
mQst_contest_info_u = U("mQst_contest_info_u", [("flowers_requested", u8), ("letter", mQst_contest_letter)])
mQst_contest_c = S("mQst_contest_c", [("base", mQst_base_c), ("requested_item", u16),
                                      ("player_id", PersonalID_c), ("type", s8),
                                      ("info", mQst_contest_info_u)])

# ---- m_museum.h / m_mail.h ----
mMsm_remail_info_c = S("mMsm_remail_info_c", [("types", u8 * 15), ("items", u16 * 30)])
mMsm_record_c = S("mMsm_record_c", [("bits", u8), ("remail_info", mMsm_remail_info_c)])
Mail_nm_c = S("Mail_nm_c", [("personalID", PersonalID_c), ("type", u8)])
Mail_hs_c = S("Mail_hs_c", [("header_back_start", s8), ("unknown", u8), ("header", u8 * 24),
                            ("footer", u8 * 32)])
Mail_hdr_c = S("Mail_hdr_c", [("recipient", Mail_nm_c), ("sender", Mail_nm_c)])
Mail_ct_c = S("Mail_ct_c", [("font", u8), ("header_back_start", u8), ("mail_type", u8),
                            ("paper_type", u8), ("header", u8 * 24), ("body", u8 * 192),
                            ("footer", u8 * 32)])
Mail_c = S("Mail_c", [("header", Mail_hdr_c), ("present", u16), ("content", Mail_ct_c)])

# ---- m_private.h (Private_c up to bank_account) ----
Inventory = S("Inventory", [("pockets", u16 * 15), ("lotto_ticket_expiry_month", u8),
                            ("lotto_ticket_mail_storage", u8), ("item_conditions", u32),
                            ("wallet", u32), ("loan", u32)])
mPr_cloth_c = S("mPr_cloth_c", [("idx", u16), ("item", u16)])
mPr_destiny_c = S("mPr_destiny_c", [("received_time", lbRTC_time_c), ("type", u8)])
mPr_birthday_c = S("mPr_birthday_c", [("year", u16), ("month", u8), ("day", u8)])
mPr_catalog_order_c = S("mPr_catalog_order_c", [("item", u16), ("shop_level", u8)])
Anmremail_c = S("Anmremail_c", [("date", lbRTC_ymd_c), ("name", u8 * 8), ("land_name", u8 * 8),
                                ("flags", u8)])
mPr_animal_memory_c = S("mPr_animal_memory_c", [("npc_id", u16), ("land_name", u8 * 8)])
mPr_map_info_c = S("mPr_map_info_c", [("land_name", u8 * 8), ("land_id", u16)])
PrivateHead = S("PrivateHead", [
    ("player_ID", PersonalID_c), ("gender", s8), ("face", s8), ("reset_count", u8),
    ("museum_record", mMsm_record_c), ("inventory", Inventory),
    ("deliveries", mQst_delivery_c * 15), ("errands", mQst_errand_c * 5), ("equipment", u16),
    ("saved_mail_header", Mail_hs_c), ("mail", Mail_c * 10), ("backgound_texture", u16),
    ("exists", u8), ("hint_count", u8), ("cloth", mPr_cloth_c), ("stored_anm_id", AnmPersonalID_c),
    ("destiny", mPr_destiny_c), ("birthday", mPr_birthday_c),
    ("catalog_orders", mPr_catalog_order_c * 5), ("unk_10A8", u8 * 24),
    ("aircheck_collect_bitfield", u32 * 2), ("remail", Anmremail_c), ("reset_code", u32),
    ("animal_memory", mPr_animal_memory_c), ("complete_fish_insect_flags", u8),
    ("celebrated_birthday_year", u16), ("furniture_collected_bitfield", u32 * 43),
    ("wall_collected_bitfield", u32 * 3), ("carpet_collected_bitfield", u32 * 3),
    ("paper_collected_bitfield", u32 * 2), ("music_collected_bitfield", u32 * 2),
    ("maps", mPr_map_info_c * 8), ("bank_account", u32),
])

# ---- m_npc.h (Animal_c) ----
Anmlnd_c = S("Anmlnd_c", [("name", u8 * 8), ("id", u16)])
Anm_bestFtr_c = S("Anm_bestFtr_c", [("check", u32), ("have_bitfield", u16)])
memuni_u = U("memuni_u", [("land", Anmlnd_c), ("island", Anm_bestFtr_c)])
Anmplmail_c = S("Anmplmail_c", [("font", u8), ("paper_type", u8), ("present", u16),
                                ("header_back_start", u8), ("header", u8 * 24), ("body", u8 * 192),
                                ("footer", u8 * 32), ("pad0", u8), ("date", lbRTC_ymd_c)])
Anmmem_c = S("Anmmem_c", [("memory_player_id", PersonalID_c), ("last_speak_time", lbRTC_time_c),
                          ("memuni", memuni_u), ("saved_town_tune", u64), ("friendship", s8),
                          ("letter_info", u8), ("letter", Anmplmail_c)])
Anmhome_c = S("Anmhome_c", [("type_unused", u8), ("block_x", u8), ("block_z", u8),
                            ("ut_x", u8), ("ut_z", u8)])
anmuni_u = U("anmuni_u", [("previous_land_name", u8 * 8), ("island_ftr", u16 * 4)])
AnmHPMail_c = S("AnmHPMail_c", [("receive_time", lbRTC_time_c), ("password", u8 * 20)])
Animal_c = S("Animal_c", [
    ("id", AnmPersonalID_c), ("memories", Anmmem_c * 7), ("home_info", Anmhome_c),
    ("catchphrase", u8 * 10), ("contest_quest", mQst_contest_c), ("parent_name", u8 * 8),
    ("anmuni", anmuni_u), ("previous_land_id", u16), ("mood", u8), ("mood_time", u8),
    ("cloth", u16), ("remove_info", u16), ("is_home", u8), ("moved_in", u8), ("removing", u8),
    ("cloth_original_id", u8), ("umbrella_id", u8), ("unk_8ED", u8), ("present_cloth", u16),
    ("animal_relations", u8 * 15), ("hp_mail", AnmHPMail_c * 4), ("unused", u8 * 24),
])

# ---- m_kabu_manager.h / m_common_data.h ----
Kabu_price_c = S("Kabu_price_c", [("daily_price", u16 * 7), ("trade_market", u16),
                                  ("update_time", lbRTC_time_c)])
SaveHead = S("SaveHead", [("save_check", mFRm_chk_t), ("scene_no", s32), ("now_npc_max", u8),
                          ("remove_animal_idx", u8), ("copy_protect", u16), ("pad_1C", u8 * 4)])
Time_c = S("Time_c", [("season", u32), ("term_idx", u32), ("bgitem_profile", s16),
                      ("bgitem_bank", s16), ("now_sec", s32), ("rtc_time", lbRTC_time_c),
                      ("rad_min", s16), ("rad_hour", s16), ("time_signal", u8), ("under_sec", u8),
                      ("disp", u8), ("rtc_crashed", u8), ("rtc_enabled", s32), ("add_sec", s32),
                      ("add_idx", s32)])
xyz_t = S("xyz_t", [("x", f32), ("y", f32), ("z", f32)])
mNpc_NpcHouseData_c = S("mNpc_NpcHouseData_c", [("type", u8), ("palette", u8), ("wall_id", u8),
                                                ("floor_id", u8), ("main_layer_id", u16),
                                                ("secondary_layer_id", u16)])
mNpc_NpcList_c = S("mNpc_NpcList_c", [("name", u16), ("field_name", u16), ("house_position", xyz_t),
                                      ("position", xyz_t), ("appear_flag", u8),
                                      ("conversation_flags", u8), ("quest_info", mQst_base_c),
                                      ("house_data", mNpc_NpcHouseData_c),
                                      ("reward_furniture", u16)])
Transition_c = S("Transition_c", [("a", u8), ("fade_rate", u8), ("wipe_rate", u8), ("wipe_type", u8)])
# common_data_t from now_private (CD+0x2613C) up to weather_intensity
CommonTail = S("CommonTail", [
    ("now_private", ptr), ("now_home", ptr), ("map_flag", u8), ("fish_location", u8),
    ("npc_is_summercamper", u8), ("player_select_animal_no", u8), ("_26148", u8 * 4),
    ("transition", Transition_c), ("bg_item_type", s16), ("bg_item_profile", s16),
    ("_26154", u8 * 0x10), ("npclist", mNpc_NpcList_c * 16), ("island_npclist", mNpc_NpcList_c * 1),
    ("house_owner_name", u16), ("last_field_id", u16), ("in_initial_block", u8),
    ("submenu_disabled", u8), ("sunlight_flag", u8), ("train_flag", u8),
    ("unused_mail_26522", Mail_c), ("_2664E", u16), ("_26650", u16),
    ("unused_mail_name_26652", Mail_nm_c), ("npc_chg_cloth", u16), ("_pad_2666A", u16),
    ("weather", s16), ("weather_intensity", s16),
])
# common_data_t from auto_nwrite_set to the end (m_common_data.h ~L310-333, m_card.h, m_event.h).
# It follows Island_agb_c, which holds a 32-byte-aligned mNW_original_design_c, so it starts on a
# 32-byte boundary; common_data_t is align 32 and 0x2DC00 bytes (foresta symbols.txt). With this
# tail 0xC0 bytes long (a multiple of 32), the only possible start is 0x2DC00 - 0xC0 = 0x2DB40.
mCD_persistent_data_c = S("mCD_persistent_data_c", [("land", mLd_land_info_c), ("pid", PersonalID_c * 4)])
CommonEnd = S("CommonEnd", [
    ("auto_nwrite_set", u8), ("select_last_select_no", u16), ("select_last_top_no", u16),
    ("travel_persistent_data", mCD_persistent_data_c), ("island_weather", s16),
    ("island_weather_intensity", s16), ("sunburn_time", s16), ("memcard_slot", u8),
    ("my_room_message_control_flags", s32), ("can_look_goki_count", s16), ("rainbow_opacity", f32),
    ("event_flags", u32 * 7),            # mEv_EVENT_TYPE_NUM == 7 (enum event_type, m_event.h)
    ("pluss_bridge_pos", ptr), ("auto_nwrite_time", lbRTC_time_c), ("rhythym_updated", u8),
    ("_2dbe1", u8), ("hem_visible", u8), ("carde_program_p", ptr), ("carde_program_size", u32),
    ("unk_nook_present_count", s32), ("pad", u8 * 16),
])
CD_SIZE = 0x2DC00
CD_END_START = CD_SIZE - C.sizeof(CommonEnd)

# ---- town map (spec "map" section) ----
# m_common_data.h: PlusBridge_c (the 1-bit fields share one u8 at +2)
PlusBridge_c = S("PlusBridge_c", [("block_x", u8), ("block_z", u8), ("flags", u8), ("build_date", lbRTC_ymd_c)])
# m_field_make.h: mFM_combo_info_c (header comment puts type at 0x05; natural layout gives 0x04)
mFM_combo_info_c = S("mFM_combo_info_c", [("bg_id", u16), ("fg_id", u16), ("type", u8)])
# m_map_ovl.h: mMP_HousePos_c
mMP_HousePos_Entry_c = S("mMP_HousePos_Entry_c", [("ut_x", u8), ("ut_z", u8), ("idx", u8)])
mMP_HousePos_c = S("mMP_HousePos_c", [("fgblock_name", u16), ("entries", mMP_HousePos_Entry_c * 3)])
# m_scene.h: Door_data_c
s_xyz = S("s_xyz", [("x", s16), ("y", s16), ("z", s16)])
Door_data_c = S("Door_data_c", [("next_scene_id", s32), ("exit_orientation", u8), ("exit_type", u8),
                                ("extra_data", u16), ("exit_position", s_xyz), ("door_actor_name", u16),
                                ("wipe_type", u8), ("pad", u8 * 3)])
# m_actor.h: ACTOR up to shape_info. mCoBG_Check_c (0x30) and Status_c (0x18) are opaque blobs
# sized from the neighbouring member offsets; the check covers the PositionAngle/xyz arithmetic.
PositionAngle = S("PositionAngle", [("position", xyz_t), ("angle", s_xyz)])
Shape_Info_head = S("Shape_Info_head", [("rotation", s_xyz), ("unk_6", s16), ("ofs_y", f32)])
ActorHead = S("ActorHead", [
    ("id", s16), ("part", u8), ("restore_fg", u8), ("scene_id", s16), ("npc_id", u16), ("block_x", s8),
    ("block_z", s8), ("move_actor_list_idx", s16), ("home", PositionAngle), ("state_bitfield", u32),
    ("actor_specific", s16), ("data_bank_id", s16), ("world", PositionAngle),
    ("last_world_position", xyz_t), ("eye", PositionAngle), ("scale", xyz_t), ("position_speed", xyz_t),
    ("speed", f32), ("gravity", f32), ("max_velocity_y", f32), ("ground_y", f32),
    ("bg_collision_check", u32 * (0x30 // 4)), ("unknown_b4", u8), ("drawn", u8), ("player_angle_y", s16),
    ("player_distance", f32), ("player_distance_xz", f32), ("player_distance_y", f32),
    ("status_data", u32 * (0x18 // 4)), ("shape_info", Shape_Info_head),
])
Actor_list = S("Actor_list", [("num_actors", s32), ("actor", ptr)])
Actor_info = S("Actor_info", [("total_num", s32), ("list", Actor_list * 7)])   # ACTOR_PART_NUM == 7
PLAY_ACTOR_INFO = 0x1DA8   # m_play.h:92 (GAME_PLAY.actor_info); the chain built on it is verified live
ACTOR_PART_PLAYER = 3


def off(struct, path):
    """Offset of a dotted member path, e.g. off(PrivateHead, 'inventory.wallet')."""
    total, t = 0, struct
    for name in path.split("."):
        f = getattr(t, name)
        total += f.offset
        t = dict(t._fields_)[name]
    return total


CD_TIME = 0x26110       # common_data.time (rtc_time at +0x10 verified live: 0x26120)
CD_NOW_PRIVATE = 0x2613C

CHECKS = [
    # (description, computed, expected)
    ("sizeof(PersonalID_c)", C.sizeof(PersonalID_c), 0x14),
    ("sizeof(AnmPersonalID_c)", C.sizeof(AnmPersonalID_c), 0xE),
    ("sizeof(mMsm_record_c)", C.sizeof(mMsm_record_c), 0x4E),
    ("sizeof(mQst_delivery_c)", C.sizeof(mQst_delivery_c), 0x28),
    ("sizeof(mQst_errand_c)", C.sizeof(mQst_errand_c), 0x58),
    ("sizeof(mQst_contest_c)", C.sizeof(mQst_contest_c), 0x28),
    ("sizeof(Mail_c)", C.sizeof(Mail_c), 0x12A),
    ("sizeof(Mail_nm_c)", C.sizeof(Mail_nm_c), 0x16),
    ("sizeof(Anmmem_c)", C.sizeof(Anmmem_c), 0x138),
    ("sizeof(Animal_c)", C.sizeof(Animal_c), 0x988),
    ("sizeof(Kabu_price_c)", C.sizeof(Kabu_price_c), 0x18),
    ("sizeof(Time_c)", C.sizeof(Time_c), 0x2C),
    ("sizeof(mNpc_NpcList_c)", C.sizeof(mNpc_NpcList_c), 0x38),
    ("sizeof(mLd_land_info_c)", C.sizeof(mLd_land_info_c), 0xC),
    # Save_t head
    ("Save.scene_no", off(SaveHead, "scene_no"), 0x14),
    ("Save.now_npc_max", off(SaveHead, "now_npc_max"), 0x18),
    ("Save.private_data (32-aligned after 0x20)", C.sizeof(SaveHead), 0x20),
    # Private_c
    ("Private.player_ID.player_id", off(PrivateHead, "player_ID.player_id"), 0x10),
    ("Private.player_ID.land_id", off(PrivateHead, "player_ID.land_id"), 0x12),
    ("Private.gender (AR)", off(PrivateHead, "gender"), 0x14),
    ("Private.face (AR)", off(PrivateHead, "face"), 0x15),
    ("Private.museum_record (0x18, not the commented 0x17)", off(PrivateHead, "museum_record"), 0x18),
    ("Private.inventory.pockets", off(PrivateHead, "inventory.pockets"), 0x68),
    ("Private.inventory.item_conditions", off(PrivateHead, "inventory.item_conditions"), 0x88),
    ("Private.inventory.wallet (verified live)", off(PrivateHead, "inventory.wallet"), 0x8C),
    ("Private.inventory.loan (AR)", off(PrivateHead, "inventory.loan"), 0x90),
    ("Private.errands", off(PrivateHead, "errands"), 0x2EC),
    ("Private.equipment", off(PrivateHead, "equipment"), 0x4A4),
    ("Private.mail", off(PrivateHead, "mail"), 0x4E0),
    ("Private.exists", off(PrivateHead, "exists"), 0x1086),
    ("Private.cloth.item", off(PrivateHead, "cloth.item"), 0x108A),
    ("Private.destiny.received_time", off(PrivateHead, "destiny.received_time"), 0x109A),
    ("Private.destiny.type", off(PrivateHead, "destiny.type"), 0x10A2),
    ("Private.birthday.month", off(PrivateHead, "birthday.month"), 0x10A6),
    ("Private.birthday.day", off(PrivateHead, "birthday.day"), 0x10A7),
    ("Private.reset_code", off(PrivateHead, "reset_code"), 0x10F4),
    ("Private.furniture_collected_bitfield", off(PrivateHead, "furniture_collected_bitfield"), 0x1108),
    ("Private.bank_account (AR)", off(PrivateHead, "bank_account"), 0x122C),
    # Animal_c
    ("Animal.id.npc_id (AR)", off(Animal_c, "id.npc_id"), 0x0),
    ("Animal.id.land_id", off(Animal_c, "id.land_id"), 0x2),
    ("Animal.id.land_name", off(Animal_c, "id.land_name"), 0x4),
    ("Animal.id.name_id", off(Animal_c, "id.name_id"), 0xC),
    ("Animal.id.looks", off(Animal_c, "id.looks"), 0xD),
    ("Animal.memories", off(Animal_c, "memories"), 0x10),
    ("Anmmem.memory_player_id.player_id", off(Anmmem_c, "memory_player_id.player_id"), 0x10),
    ("Anmmem.memory_player_id.land_id", off(Anmmem_c, "memory_player_id.land_id"), 0x12),
    ("Anmmem.friendship", off(Anmmem_c, "friendship"), 0x30),
    ("Anmmem.last_speak_time.day", off(Anmmem_c, "last_speak_time") + 3, 0x17),
    ("Anmmem.last_speak_time.month", off(Anmmem_c, "last_speak_time") + 5, 0x19),
    ("Anmmem.last_speak_time.year", off(Anmmem_c, "last_speak_time") + 6, 0x1A),
    ("Animal.home_info.block_x", off(Animal_c, "home_info.block_x"), 0x899),
    ("Animal.home_info.block_z", off(Animal_c, "home_info.block_z"), 0x89A),
    ("Animal.home_info.ut_x", off(Animal_c, "home_info.ut_x"), 0x89B),
    ("Animal.home_info.ut_z", off(Animal_c, "home_info.ut_z"), 0x89C),
    ("Animal.catchphrase", off(Animal_c, "catchphrase"), 0x89D),
    ("Animal.contest_quest", off(Animal_c, "contest_quest"), 0x8A8),
    ("Animal.mood", off(Animal_c, "mood"), 0x8E2),
    ("Animal.cloth (AR)", off(Animal_c, "cloth"), 0x8E4),
    ("Animal.is_home", off(Animal_c, "is_home"), 0x8E8),
    ("Animal.moved_in", off(Animal_c, "moved_in"), 0x8E9),
    ("Animal.removing", off(Animal_c, "removing"), 0x8EA),
    ("Animal.animal_relations", off(Animal_c, "animal_relations"), 0x8F0),
    ("Animal.hp_mail", off(Animal_c, "hp_mail"), 0x900),
    # Kabu_price_c (base CD+0x20480 is AR)
    ("Kabu.trade_market", off(Kabu_price_c, "trade_market"), 0xE),
    ("Kabu.update_time.day", off(Kabu_price_c, "update_time.day"), 0x13),
    ("Kabu.update_time.month", off(Kabu_price_c, "update_time.month"), 0x15),
    ("Kabu.update_time.year", off(Kabu_price_c, "update_time.year"), 0x16),
    # Time_c anchored on verified rtc_time (CD+0x26120)
    ("CD.time.rtc_time (verified live)", CD_TIME + off(Time_c, "rtc_time"), 0x26120),
    ("CD.time.rtc_time.weekday", CD_TIME + off(Time_c, "rtc_time.weekday"), 0x26124),
    ("CD.now_private = time + sizeof(Time_c)", CD_TIME + C.sizeof(Time_c), CD_NOW_PRIVATE),
    # common_data tail from now_private
    ("CD.npclist", CD_NOW_PRIVATE + off(CommonTail, "npclist"), 0x26164),
    ("CD.island_npclist", CD_NOW_PRIVATE + off(CommonTail, "island_npclist"), 0x264E4),
    ("CD.unused_mail (header comment says 0x26522; real 0x26524)",
     CD_NOW_PRIVATE + off(CommonTail, "unused_mail_26522"), 0x26524),
    ("CD._2664E", CD_NOW_PRIVATE + off(CommonTail, "_2664E"), 0x2664E),
    ("CD.npc_chg_cloth", CD_NOW_PRIVATE + off(CommonTail, "npc_chg_cloth"), 0x26668),
    ("CD.weather", CD_NOW_PRIVATE + off(CommonTail, "weather"), 0x2666C),
    ("CD.weather_intensity", CD_NOW_PRIVATE + off(CommonTail, "weather_intensity"), 0x2666E),
    # common_data end, anchored on sizeof(common_data_t) == 0x2DC00
    ("sizeof(mCD_persistent_data_c)", C.sizeof(mCD_persistent_data_c), 0x5C),
    ("sizeof(common_data tail from auto_nwrite_set) % 32 == 0", C.sizeof(CommonEnd) % 32, 0),
    ("CD.auto_nwrite_set (header comment 0x2DB40)", CD_END_START, 0x2DB40),
    ("CD.island_weather", CD_END_START + off(CommonEnd, "island_weather"), 0x2DBA2),
    ("CD.island_weather_intensity", CD_END_START + off(CommonEnd, "island_weather_intensity"), 0x2DBA4),
    ("CD.event_flags (header comment 0x2DBB8)", CD_END_START + off(CommonEnd, "event_flags"), 0x2DBB8),
    ("CD.pad (header comment 0x2DBF0)", CD_END_START + off(CommonEnd, "pad"), 0x2DBF0),
    # town map
    ("sizeof(PlusBridge_c)", C.sizeof(PlusBridge_c), 0x8),
    ("PlusBridge.flags (exists = bit 7)", off(PlusBridge_c, "flags"), 0x2),
    ("sizeof(mFM_combo_info_c)", C.sizeof(mFM_combo_info_c), 0x6),
    ("mFM_combo_info.fg_id", off(mFM_combo_info_c, "fg_id"), 0x2),
    ("mFM_combo_info.type (header comment 0x05)", off(mFM_combo_info_c, "type"), 0x4),
    ("sizeof(mMP_HousePos_c)", C.sizeof(mMP_HousePos_c), 0xC),
    ("mMP_HousePos.entries", off(mMP_HousePos_c, "entries"), 0x2),
    ("sizeof(Door_data_c)", C.sizeof(Door_data_c), 0x14),
    ("Door_data.exit_position", off(Door_data_c, "exit_position"), 0x8),
    ("CD.structure_exit_door_data.exit_position", 0x28544 + off(Door_data_c, "exit_position"), 0x2854C),
    ("mNpc_NpcList.house_position.y", off(mNpc_NpcList_c, "house_position.y"), 0x8),
    ("ACTOR.world.position (verified live)", off(ActorHead, "world.position"), 0x28),
    ("ACTOR.world.angle", off(ActorHead, "world.angle"), 0x34),
    ("ACTOR.last_world_position", off(ActorHead, "last_world_position"), 0x3C),
    ("ACTOR.bg_collision_check", off(ActorHead, "bg_collision_check"), 0x84),
    ("ACTOR.status_data", off(ActorHead, "status_data"), 0xC4),
    ("ACTOR.shape_info.rotation.y (verified live)", off(ActorHead, "shape_info.rotation.y"), 0xDE),
    ("play.actor_info.list[PLAYER].num_actors",
     PLAY_ACTOR_INFO + off(Actor_info, "list") + ACTOR_PART_PLAYER * C.sizeof(Actor_list), 0x1DC4),
    ("play.actor_info.list[PLAYER].actor (verified live)",
     PLAY_ACTOR_INFO + off(Actor_info, "list") + ACTOR_PART_PLAYER * C.sizeof(Actor_list)
     + off(Actor_list, "actor"), 0x1DC8),
]


def main():
    bad = 0
    for desc, got, want in CHECKS:
        ok = got == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {desc:58s} computed=0x{got:X} expected=0x{want:X}")
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} layout checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
