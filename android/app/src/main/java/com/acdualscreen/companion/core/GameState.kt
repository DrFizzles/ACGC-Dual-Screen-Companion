package com.acdualscreen.companion.core

/** What the panel should be showing, from "nothing yet" to "full town data". */
enum class Phase {
    SPEC_MISSING,   // no usable ac_memory_map.json
    CONNECTING,     // poller just started
    NO_DOLPHIN,     // no reply from the EmuLink server
    WRONG_GAME,     // some other game / revision is running
    NOT_IN_TOWN,    // right game, but not in gameplay (title screen, menus, loading)
    IN_TOWN,        // gameplay: data below is meaningful
}

data class Clock(
    val year: Int,
    val month: Int,
    val day: Int,
    val weekday: Int,
    val weekdayLabel: String?,
    val hour: Int,
    val minute: Int,
    val monthLabel: String? = null,
)

/** [name] is null when it could not be resolved; [empty] marks the empty-slot sentinel. */
data class Pocket(val slot: Int, val id: Int, val name: String?, val empty: Boolean, val cond: Int) {
    companion object {
        const val COND_NORMAL = 0
        const val COND_PRESENT = 1
        const val COND_QUEST = 2
    }
}

data class Villager(val slot: Int, val id: Int, val name: String?)

/** The town's native fruit (town_fruit item id = 0x2800 + ordinal, m_name_table.h). */
enum class Fruit(val label: String) {
    APPLE("Apple"), CHERRY("Cherry"), PEAR("Pear"), PEACH("Peach"), ORANGE("Orange");

    companion object {
        const val FIRST_ID = 0x2800
        fun fromItemId(id: Int?): Fruit? = id?.let { entries.getOrNull(it - FIRST_ID) }
    }
}

/** House loan: still paying, everything paid off so far, or never had one (start of the game). */
enum class LoanState { PAYING, PAID_OFF, NONE }

/** The current player's glowing spot today. */
enum class ShineSpot { NOT_DUG, DUG, NONE_TODAY, UNKNOWN }

/**
 * Daily-tracker data, read only while a visible panel shows the tracker.
 * [talkedToday] is keyed by villager slot (null = could not tell); [fossilsBuried] null = unknown.
 */
data class DailyState(
    val talkedToday: Map<Int, Boolean?> = emptyMap(),
    val fossilsBuried: Int? = null,
    val fossilMax: Int = 5,
    val shineSpot: ShineSpot = ShineSpot.UNKNOWN,
    val note: String = "",
) {
    /** Fossils dug today: the daily renewal tops buried fossils up to [fossilMax]. */
    val fossilsDug: Int? get() = fossilsBuried?.let { (fossilMax - it).coerceIn(0, fossilMax) }
}

/** [price] is null when the value read was impossible (> 2000, i.e. a torn or bad read). */
data class Turnip(val label: String, val price: Int?, val today: Boolean)

/**
 * Everything the panel renders. Immutable and comparable, so the view can skip redraws
 * when nothing changed. Fields that could not be read are null.
 */
data class GameState(
    val phase: Phase,
    val status: String,
    val gameId: String? = null,
    val town: String? = null,
    val scene: String? = null,
    val playerNo: Int? = null,
    /** 0..3 when now_private points at a local player; null when visiting/unknown. */
    val playerIndex: Int? = null,
    val playerName: String? = null,
    val playerTown: String? = null,
    val clock: Clock? = null,
    val weather: String? = null,
    val wallet: Long? = null,
    val bank: Long? = null,
    val loan: Long? = null,
    val loanState: LoanState? = null,
    /** Name of the held tool/umbrella; null when nothing is held or it could not be read. */
    val heldItem: String? = null,
    /** (month, day), or null when no birthday has been entered. */
    val birthday: Pair<Int, Int>? = null,
    /** Today's fortune label; null when the player has not had a fortune today. */
    val fortune: String? = null,
    val fruit: Fruit? = null,
    val season: String? = null,
    val pockets: List<Pocket> = emptyList(),
    val turnips: List<Turnip> = emptyList(),
    val villagers: List<Villager> = emptyList(),
    /** Extra scalar globals the spec defines beyond the required keys. */
    val extras: List<Pair<String, String>> = emptyList(),
    /** Town map (layout, houses, player marker); null unless a view asked for the map. */
    val map: TownMapState? = null,
    /** Daily tracker; null unless a view asked for it. */
    val daily: DailyState? = null,
) {
    val visiting: Boolean get() = phase == Phase.IN_TOWN && playerIndex == null

    /** This state without the town map, for views that do not draw it (so a moving marker does not redraw them). */
    fun withoutMap(): GameState = if (map == null) this else copy(map = null)

    /** This state without the map and the daily tracker (for the info page). */
    fun withoutExtras(): GameState = if (map == null && daily == null) this else copy(map = null, daily = null)

    companion object {
        fun of(phase: Phase, status: String) = GameState(phase, status)
    }
}
