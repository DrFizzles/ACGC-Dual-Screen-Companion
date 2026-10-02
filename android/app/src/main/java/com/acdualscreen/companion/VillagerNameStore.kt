package com.acdualscreen.companion

import android.content.Context
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/**
 * Remembers villager names the poller has learned (npc id -> name), per game image, so they do
 * not have to be learned again after a restart. A name belongs to a fixed table in the game, so
 * a saved pair stays right for every town played on that image.
 */
class VillagerNameStore(context: Context) : Poller.NameStore {

    companion object {
        private const val BUNDLED_ASSET = "villager_names.json"
        private val bundledCache = AtomicReference<Map<Int, String>?>(null)

        /**
         * Every villager's name, from assets/villager_names.json (built from the game's
         * npc_name_str_table.bin by tools/pc_client/extract_villager_names.py). Empty if absent.
         */
        fun bundled(context: Context): Map<Int, String> {
            bundledCache.get()?.let { return it }
            val names = try {
                parse(context.assets.open(BUNDLED_ASSET).bufferedReader(Charsets.UTF_8).use { it.readText() })
            } catch (e: Exception) {
                emptyMap()
            }
            bundledCache.set(names)
            return names
        }

        fun parse(text: String): Map<Int, String> {
            val o = JSONObject(text)
            return o.keys().asSequence().mapNotNull { k -> k.toIntOrNull(16)?.let { it to o.getString(k) } }.toMap()
        }
    }
    private val sp = context.applicationContext.getSharedPreferences("villager_names", Context.MODE_PRIVATE)

    override fun load(gameKey: String): Map<Int, String> {
        val text = sp.getString(gameKey, null) ?: return emptyMap()
        return try {
            parse(text)
        } catch (e: Exception) {
            emptyMap()
        }
    }

    override fun save(gameKey: String, names: Map<Int, String>) {
        val o = JSONObject()
        for ((id, n) in names) o.put("%04X".format(id), n)
        sp.edit().putString(gameKey, o.toString()).apply()
    }
}
