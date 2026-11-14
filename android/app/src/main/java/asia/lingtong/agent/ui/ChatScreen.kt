package asia.lingtong.agent.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.data.ChatMessageEntity
import asia.lingtong.agent.data.ChatTurn
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import asia.lingtong.agent.ui.theme.BaSkyLight
import kotlinx.coroutines.launch

@Composable
fun ChatScreen(container: AppContainer) {
    val dao = container.db.chatMessages()
    val scope = rememberCoroutineScope()
    val messages = remember { mutableStateListOf<ChatMessageEntity>() }
    val listState = rememberLazyListState()

    var input by rememberSaveable { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var speak by rememberSaveable { mutableStateOf(true) }
    var status by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        messages.clear()
        messages.addAll(dao.recent(200).reversed())
    }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.lastIndex)
        }
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || sending) return
        scope.launch {
            sending = true
            status = "正在思考…"
            try {
                // 先取历史再落库，避免本轮用户消息重复进上下文。
                val history = dao.recent(12).reversed().map { ChatTurn(it.role, it.content) }
                input = ""
                val userMessage = ChatMessageEntity(role = "user", content = text)
                messages.add(userMessage.copy(id = dao.insert(userMessage)))

                val reply = container.api.chatText(text, history)
                val replyMessage = ChatMessageEntity(role = "assistant", content = reply)
                messages.add(replyMessage.copy(id = dao.insert(replyMessage)))
                status = ""

                if (speak && reply.isNotBlank()) {
                    status = "正在合成语音…"
                    val speakText = reply.take(500)
                    runCatching { container.api.chatTts(speakText) }
                        .onSuccess {
                            container.player.play(it)
                            status = ""
                        }
                        .onFailure { status = "语音失败：${it.message}" }
                }
            } catch (exc: Exception) {
                status = "发送失败：${exc.message}"
            } finally {
                sending = false
            }
        }
    }

    BaScreen {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .imePadding(),
        ) {
            BaTopBar(
                title = "对话",
                subtitle = "TALK WITH XIAOLING",
            ) {
                SpeakToggle(
                    speak = speak,
                    onToggle = {
                        speak = it
                        if (!it) container.player.stop()
                    },
                )
            }

            if (messages.isEmpty()) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .padding(horizontal = 24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    BaEmptyState(
                        title = "还没有对话记录",
                        hint = "先出文字，再播语音。发一句话试试。",
                    )
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        start = 12.dp,
                        end = 12.dp,
                        top = 12.dp,
                        bottom = 10.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(messages) { message -> Bubble(message) }
                }
            }

            if (status.isNotBlank()) {
                Row(
                    modifier = Modifier
                        .padding(horizontal = 14.dp)
                        .clip(RoundedCornerShape(50))
                        .background(Color.White.copy(alpha = 0.85f))
                        .padding(horizontal = 10.dp, vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(5.dp)
                            .background(BaSkyDeep, CircleShape),
                    )
                    Text(
                        text = status,
                        style = MaterialTheme.typography.bodySmall,
                        color = BaNavySoft,
                    )
                }
            }

            InputBar(
                value = input,
                onValueChange = { input = it.take(1000) },
                sending = sending,
                onSend = { send() },
            )
        }
    }
}

/** 播报开关：浅蓝胶囊 + 状态点，选中即开。 */
@Composable
private fun SpeakToggle(speak: Boolean, onToggle: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(if (speak) BaSkyLight else Color.White)
            .clickable { onToggle(!speak) }
            .padding(horizontal = 11.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .background(if (speak) BaSkyDeep else BaNavySoft.copy(alpha = 0.35f), CircleShape),
        )
        Text(
            text = if (speak) "播报 ON" else "播报 OFF",
            style = MaterialTheme.typography.labelSmall,
            color = if (speak) BaSkyDeep else BaNavySoft,
            letterSpacing = 1.sp,
        )
    }
}

@Composable
private fun Bubble(message: ChatMessageEntity) {
    val mine = message.role == "user"
    val bubbleShape = rememberCutShape(10.dp)

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Top,
    ) {
        if (!mine) {
            Box(
                modifier = Modifier
                    .padding(top = 16.dp, end = 8.dp)
                    .size(28.dp)
                    .clip(CircleShape)
                    .background(BaSkyLight),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "绫",
                    style = MaterialTheme.typography.labelMedium,
                    color = BaSkyDeep,
                )
            }
        }
        Column(
            horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text = if (mine) "YOU" else "XIAOLING",
                style = MaterialTheme.typography.labelSmall,
                color = if (mine) BaNavySoft.copy(alpha = 0.6f) else BaSkyDeep,
                letterSpacing = 1.8.sp,
            )
            Surface(
                modifier = Modifier.widthIn(max = 300.dp),
                shape = bubbleShape,
                color = if (mine) Color.Transparent else Color.White,
                border = if (mine) null else BorderStroke(1.dp, BaLine),
            ) {
                Box(
                    modifier = Modifier
                        .background(
                            if (mine) {
                                Brush.horizontalGradient(listOf(BaSkyDeep, BaSky))
                            } else {
                                Brush.horizontalGradient(listOf(Color.White, Color.White))
                            }
                        )
                        .padding(horizontal = 13.dp, vertical = 9.dp),
                ) {
                    if (!mine) {
                        Box(
                            modifier = Modifier
                                .align(Alignment.TopStart)
                                .padding(top = 4.dp)
                                .size(width = 2.5.dp, height = 14.dp)
                                .background(BaSky, RoundedCornerShape(2.dp)),
                        )
                    }
                    Text(
                        text = message.content,
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (mine) Color.White else BaNavy,
                        modifier = if (mine) Modifier else Modifier.padding(start = 9.dp),
                    )
                }
            }
        }
        if (mine) {
            Spacer(Modifier.width(2.dp))
        }
    }
}

/** 底部输入栏：白底细线，输入框 + 发送按钮。 */
@Composable
private fun InputBar(
    value: String,
    onValueChange: (String) -> Unit,
    sending: Boolean,
    onSend: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.White.copy(alpha = 0.95f)),
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
                .padding(horizontal = 12.dp, vertical = 9.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            BaTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text("输入消息…") },
                maxLines = 4,
                modifier = Modifier.weight(1f),
            )
            BaButton(
                onClick = onSend,
                enabled = !sending && value.isNotBlank(),
            ) {
                Text(if (sending) "等待" else "发送")
            }
        }
    }
}
