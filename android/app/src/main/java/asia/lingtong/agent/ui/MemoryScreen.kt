package asia.lingtong.agent.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.data.MemoryItem
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
fun MemoryScreen(container: AppContainer, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var memories by remember { mutableStateOf<List<MemoryItem>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var deleting by remember { mutableStateOf<Long?>(null) }

    suspend fun reload() {
        loading = true
        error = null
        runCatching { container.api.fetchMemories() }
            .onSuccess { memories = it }
            .onFailure { error = it.message ?: "加载失败" }
        loading = false
    }

    fun deleteOne(item: MemoryItem) {
        scope.launch {
            deleting = item.id
            runCatching { container.api.deleteMemory(item.id) }
                .onSuccess { memories = memories.filter { it.id != item.id } }
            deleting = null
        }
    }

    LaunchedEffect(Unit) { reload() }

    BaScreen {
        Column(modifier = Modifier.fillMaxSize()) {
            BaTopBar(
                title = "我的记忆",
                subtitle = "LONG-TERM MEMORY",
                onBack = onClose,
            ) {
                BaOutlineButton(
                    onClick = { scope.launch { reload() } },
                    enabled = !loading,
                ) {
                    Text("刷新")
                }
            }

            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 12.dp)
                    .padding(top = 10.dp, bottom = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Hint("AI 从对话里自动提炼出的长期事实，按账号隔离，跨设备共享。可手动删除。")

                if (loading) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        BaEmptyState(title = "加载中…", hint = "正在同步账号记忆")
                    }
                } else if (error != null) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        BaEmptyState(title = "加载失败", hint = error ?: "未知错误")
                    }
                } else if (memories.isEmpty()) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        BaEmptyState(
                            title = "还没有任何记忆",
                            hint = "多聊几次，AI 就会慢慢把重要事实存下来。",
                        )
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(memories) { item ->
                            MemoryRow(
                                item = item,
                                busy = deleting == item.id,
                                onDelete = { deleteOne(item) },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MemoryRow(item: MemoryItem, busy: Boolean, onDelete: () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = rememberCutShape(12.dp),
        color = Color.White,
        border = BorderStroke(1.dp, BaLine),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            Row(verticalAlignment = Alignment.Top) {
                Box(
                    modifier = Modifier
                        .padding(top = 6.dp)
                        .size(6.dp)
                        .background(BaSkyDeep, CircleShape),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = item.fact,
                    style = MaterialTheme.typography.bodyMedium,
                    color = BaNavy,
                    modifier = Modifier.weight(1f),
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "${item.source} · ${formatMemoryTime(item.createdAt)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = BaNavySoft,
                )
                BaTextButton(onClick = onDelete, enabled = !busy) {
                    Text(if (busy) "删除中" else "删除")
                }
            }
        }
    }
}

private fun formatMemoryTime(iso: String): String = runCatching {
    val instant = Instant.parse(iso)
    DateTimeFormatter.ofPattern("MM-dd HH:mm").format(
        instant.atZone(ZoneId.systemDefault()).toLocalDateTime()
    )
}.getOrDefault(iso.take(16))
