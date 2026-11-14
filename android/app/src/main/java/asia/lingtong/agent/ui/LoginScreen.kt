package asia.lingtong.agent.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.R
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import kotlinx.coroutines.launch

@Composable
fun LoginScreen(container: AppContainer, onLoggedIn: () -> Unit) {
    val session = container.session
    val scope = rememberCoroutineScope()

    var server by rememberSaveable { mutableStateOf(session.serverUrl) }
    var username by rememberSaveable { mutableStateOf(session.account?.username ?: "") }
    var password by rememberSaveable { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    fun submit() {
        if (busy || username.isBlank() || password.isBlank()) return
        scope.launch {
            busy = true
            error = ""
            try {
                session.serverUrl = server
                container.api.login(username.trim(), password)
                password = ""
                onLoggedIn()
            } catch (exc: Exception) {
                error = "登录失败：${exc.message}"
            } finally {
                busy = false
            }
        }
    }

    BaScreen {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = 22.dp, vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(18.dp))
            Box(contentAlignment = Alignment.Center) {
                BaHalo(modifier = Modifier.size(148.dp))
                Image(
                    painter = painterResource(R.drawable.app_logo),
                    contentDescription = "小绫",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(84.dp)
                        .clip(CircleShape)
                        .border(3.dp, Color.White, CircleShape),
                )
            }
            Spacer(Modifier.height(14.dp))
            Text(
                text = "桌面的小绫",
                style = MaterialTheme.typography.displaySmall,
                color = BaNavy,
                letterSpacing = 2.sp,
            )
            Spacer(Modifier.height(6.dp))
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    modifier = Modifier
                        .width(22.dp)
                        .height(1.5.dp)
                        .background(BaSky.copy(alpha = 0.6f)),
                )
                Text(
                    text = "XIAOLING · AGENT",
                    style = MaterialTheme.typography.labelSmall,
                    color = BaSkyDeep,
                    letterSpacing = 3.sp,
                )
                Box(
                    modifier = Modifier
                        .width(22.dp)
                        .height(1.5.dp)
                        .background(BaSky.copy(alpha = 0.6f)),
                )
            }
            Spacer(Modifier.height(22.dp))

            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = rememberCutShape(16.dp),
                color = Color.White,
                border = BorderStroke(1.dp, BaLine),
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(7.dp)
                                .background(BaSkyDeep, RoundedCornerShape(1.dp)),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = "登录面板",
                            style = MaterialTheme.typography.titleSmall,
                            color = BaNavy,
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            text = "SIGN IN",
                            style = MaterialTheme.typography.labelSmall,
                            color = BaSkyDeep,
                            letterSpacing = 2.sp,
                        )
                    }
                    Hint("使用面板账号登录；令牌会加密保存在本机并自动续期。服务器地址已预设为 https://agent.lingtong.asia/agent，如需修改请到「设置」页。")

                    BaTextField(
                        value = username,
                        onValueChange = { username = it },
                        label = { Text("账号") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    BaTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("密码") },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth(),
                    )

                    if (error.isNotBlank()) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(8.dp))
                                .background(MaterialTheme.colorScheme.error.copy(alpha = 0.1f))
                                .padding(horizontal = 10.dp, vertical = 7.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "!",
                                style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.error,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                text = error,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }

                    BaButton(
                        onClick = { submit() },
                        enabled = !busy && username.isNotBlank() && password.isNotBlank(),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (busy) {
                            CircularProgressIndicator(
                                modifier = Modifier
                                    .padding(end = 8.dp)
                                    .size(16.dp),
                                strokeWidth = 2.dp,
                                color = Color.White,
                            )
                        }
                        Text(if (busy) "登录中…" else "连接机器人")
                    }

                    // 兜底：当 serverUrl 被改成局域网/旧地址导致登录失败时，允许一键回到默认公网地址。
                    if (server != asia.lingtong.agent.data.DEFAULT_SERVER) {
                        Spacer(Modifier.height(10.dp))
                        androidx.compose.material3.TextButton(onClick = {
                            server = asia.lingtong.agent.data.DEFAULT_SERVER
                            session.serverUrl = server
                            error = ""
                        }) {
                            Text("服务器地址非默认，点击重置为 ${asia.lingtong.agent.data.DEFAULT_SERVER}")
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Spacer(Modifier.height(22.dp))
            Text(
                text = "BLUE ARCHIVE STYLE UI",
                style = MaterialTheme.typography.labelSmall,
                color = BaNavySoft.copy(alpha = 0.5f),
                letterSpacing = 3.sp,
                textAlign = TextAlign.Center,
            )
        }
    }
}
