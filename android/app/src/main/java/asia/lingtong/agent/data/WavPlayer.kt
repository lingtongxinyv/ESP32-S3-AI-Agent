package asia.lingtong.agent.data

import android.content.Context
import android.media.MediaPlayer
import java.io.File

/** 把服务端返回的 WAV 落到缓存目录再用 MediaPlayer 播放，避免边下边播的格式问题。 */
class WavPlayer(private val context: Context) {

    private var player: MediaPlayer? = null

    fun play(bytes: ByteArray) {
        stop()
        val file = File(context.cacheDir, "reply.wav")
        file.writeBytes(bytes)
        player = MediaPlayer().apply {
            setDataSource(file.absolutePath)
            prepare()
            start()
        }
    }

    fun stop() {
        player?.runCatching { stop() }
        player?.release()
        player = null
    }
}
