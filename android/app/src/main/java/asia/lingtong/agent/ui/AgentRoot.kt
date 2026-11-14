package asia.lingtong.agent.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.ui.theme.BaBg
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSkyDeep
import asia.lingtong.agent.ui.theme.BaSkyLight

private val TABS = listOf("对话", "机器人", "设置")
private val TAB_KINDS = listOf(BaTabKind.Chat, BaTabKind.Robot, BaTabKind.Settings)
private val TAB_EN = listOf("TALK", "DEVICE", "CONFIG")

@Composable
fun AgentRoot(container: AppContainer) {
    val context = LocalContext.current
    var loggedIn by remember { mutableStateOf(container.session.loggedIn) }

    if (!loggedIn) {
        LoginScreen(
            container = container,
            onLoggedIn = { loggedIn = true },
        )
    } else {
        var tab by rememberSaveable { mutableIntStateOf(0) }
        Scaffold(
            containerColor = BaBg,
            bottomBar = {
                BaBottomBar(selected = tab, onSelect = { tab = it })
            },
        ) { padding ->
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
            ) {
                when (tab) {
                    0 -> ChatScreen(container)
                    1 -> RobotScreen(container)
                    else -> SettingsScreen(
                        container = container,
                        onLogout = {
                            container.player.stop()
                            container.session.clear()
                            loggedIn = false
                        },
                        appVersion = context.packageManager
                            .getPackageInfo(context.packageName, 0)
                            .versionName
                            ?: "0.1.0",
                    )
                }
            }
        }
    }
}

/** 底部导航：白底、顶部细线，选中项带浅蓝胶囊与蓝色标记。 */
@Composable
private fun BaBottomBar(selected: Int, onSelect: (Int) -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(BaLine),
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            TABS.forEachIndexed { index, label ->
                val active = selected == index
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .clickable { onSelect(index) }
                        .padding(vertical = 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .height(28.dp)
                            .size(width = 52.dp, height = 28.dp)
                            .clip(RoundedCornerShape(10.dp))
                            .background(if (active) BaSkyLight else androidx.compose.ui.graphics.Color.Transparent),
                        contentAlignment = Alignment.Center,
                    ) {
                        BaTabIcon(kind = TAB_KINDS[index], selected = active)
                    }
                    Text(
                        text = label,
                        style = MaterialTheme.typography.labelMedium,
                        color = if (active) BaSkyDeep else BaNavySoft,
                    )
                    Text(
                        text = TAB_EN[index],
                        style = MaterialTheme.typography.labelSmall,
                        color = if (active) BaSkyDeep.copy(alpha = 0.7f) else BaNavySoft.copy(alpha = 0.45f),
                    )
                }
            }
        }
    }
}
