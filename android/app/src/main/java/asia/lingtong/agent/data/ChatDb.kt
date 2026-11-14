package asia.lingtong.agent.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "chat_message")
data class ChatMessageEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val role: String,
    val content: String,
    val deviceId: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)

@Dao
interface ChatMessageDao {

    @Query("SELECT * FROM chat_message ORDER BY id ASC")
    suspend fun all(): List<ChatMessageEntity>

    @Query("SELECT * FROM chat_message ORDER BY id DESC LIMIT :limit")
    suspend fun recent(limit: Int): List<ChatMessageEntity>

    @Query("SELECT COUNT(*) FROM chat_message")
    suspend fun count(): Int

    @Insert
    suspend fun insert(message: ChatMessageEntity): Long

    @Insert
    suspend fun insertAll(messages: List<ChatMessageEntity>)

    @Query("DELETE FROM chat_message")
    suspend fun clear()
}

@Database(entities = [ChatMessageEntity::class], version = 1, exportSchema = false)
abstract class AgentDatabase : RoomDatabase() {

    abstract fun chatMessages(): ChatMessageDao

    companion object {
        fun build(context: Context): AgentDatabase =
            Room.databaseBuilder(context, AgentDatabase::class.java, "agent.db").build()
    }
}
