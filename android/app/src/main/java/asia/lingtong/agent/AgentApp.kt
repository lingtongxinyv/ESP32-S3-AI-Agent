package asia.lingtong.agent

import android.app.Application
import android.content.Context
import asia.lingtong.agent.data.AgentDatabase
import asia.lingtong.agent.data.ApiClient
import asia.lingtong.agent.data.ConfigStore
import asia.lingtong.agent.data.SessionStore
import asia.lingtong.agent.data.WavPlayer

class AppContainer(context: Context) {
    val session = SessionStore(context)
    val api = ApiClient(session)
    val config = ConfigStore(context)
    val db = AgentDatabase.build(context)
    val player = WavPlayer(context)
}

class AgentApp : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
