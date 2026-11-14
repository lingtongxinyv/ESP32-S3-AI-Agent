package asia.lingtong.agent.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.GenericShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import asia.lingtong.agent.ui.theme.BaBg
import asia.lingtong.agent.ui.theme.BaBgTop
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaOrange
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep

/** 页面底色：淡蓝渐层 + 右上斜纹 + 光环装饰。 */
@Composable
fun BaScreen(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(BaBgTop, BaBg, Color(0xFFE2EFFB)),
                )
            ),
    ) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            clipRect(
                left = size.width * 0.5f,
                top = 0f,
                right = size.width,
                bottom = size.height * 0.17f,
            ) {
                val gap = 22.dp.toPx()
                var x = size.width * 0.4f
                while (x < size.width * 1.2f) {
                    drawLine(
                        color = BaSky.copy(alpha = 0.07f),
                        start = Offset(x, 0f),
                        end = Offset(x - size.height * 0.17f, size.height * 0.17f),
                        strokeWidth = 9.dp.toPx(),
                    )
                    x += gap
                }
            }
            drawCircle(
                color = BaSky.copy(alpha = 0.13f),
                radius = size.width * 0.34f,
                center = Offset(size.width * 1.02f, 0f),
                style = Stroke(width = 1.5.dp.toPx()),
            )
            drawCircle(
                color = BaSky.copy(alpha = 0.08f),
                radius = size.width * 0.24f,
                center = Offset(size.width * 1.02f, 0f),
                style = Stroke(width = 1.5.dp.toPx()),
            )
            drawCircle(
                color = BaSky.copy(alpha = 0.06f),
                radius = size.width * 0.4f,
                center = Offset(0f, size.height),
                style = Stroke(width = 1.5.dp.toPx()),
            )
        }
        content()
    }
}

/** 切角矩形：右上 + 左下被斜切，是整套 UI 的基础形状。 */
@Composable
fun rememberCutShape(cut: Dp = 12.dp): Shape {
    val cutPx = with(LocalDensity.current) { cut.toPx() }
    return remember(cutPx) {
        GenericShape { size, _ ->
            val c = cutPx.coerceAtMost(minOf(size.width, size.height) / 2f)
            if (c <= 0f) {
                addRect(androidx.compose.ui.geometry.Rect(0f, 0f, size.width, size.height))
            } else {
                moveTo(0f, 0f)
                lineTo(size.width - c, 0f)
                lineTo(size.width, c)
                lineTo(size.width, size.height)
                lineTo(c, size.height)
                lineTo(0f, size.height - c)
                close()
            }
        }
    }
}

val BaPillShape = RoundedCornerShape(50)

/** 页头：小徽记 + 中文标题 + 英文小字，底部带一段蓝色标记线。 */
@Composable
fun BaTopBar(
    title: String,
    subtitle: String? = null,
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    trailing: @Composable RowScope.() -> Unit = {},
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(
                Brush.verticalGradient(
                    listOf(Color.White.copy(alpha = 0.94f), Color.White.copy(alpha = 0.62f)),
                )
            ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 12.dp, top = 10.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (onBack != null) {
                Box(
                    modifier = Modifier
                        .padding(end = 8.dp)
                        .size(34.dp)
                        .clip(CircleShape)
                        .background(Color.White)
                        .border(1.dp, BaLine, CircleShape)
                        .clickable { onBack() },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        "‹",
                        style = MaterialTheme.typography.titleLarge,
                        color = BaSkyDeep,
                        modifier = Modifier.padding(bottom = 2.dp),
                    )
                }
            } else {
                BaEmblem(modifier = Modifier.padding(end = 10.dp))
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleLarge,
                    color = BaNavy,
                    letterSpacing = 1.5.sp,
                )
                if (subtitle != null) {
                    Text(
                        text = subtitle,
                        style = MaterialTheme.typography.labelSmall,
                        color = BaSkyDeep,
                        letterSpacing = 2.2.sp,
                    )
                }
            }
            trailing()
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(width = 72.dp, height = 2.dp)
                    .background(
                        Brush.horizontalGradient(listOf(BaSkyDeep, BaSky)),
                    )
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(2.dp)
                    .background(BaLine.copy(alpha = 0.75f)),
            )
        }
    }
}

/** 小徽记：倾斜方块 + 光环。 */
@Composable
fun BaEmblem(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier.size(26.dp)) {
        val w = size.width
        val h = size.height
        drawCircle(
            color = BaSky.copy(alpha = 0.55f),
            radius = w * 0.46f,
            center = Offset(w * 0.5f, h * 0.5f),
            style = Stroke(width = 1.4.dp.toPx()),
        )
        drawCircle(
            color = BaSkyDeep,
            radius = w * 0.16f,
            center = Offset(w * 0.5f, h * 0.5f),
        )
        rotate(degrees = 45f, pivot = Offset(w * 0.5f, h * 0.5f)) {
            drawRect(
                color = BaSkyDeep,
                topLeft = Offset(w * 0.29f, h * 0.29f),
                size = Size(w * 0.42f, h * 0.42f),
                style = Stroke(width = 1.6.dp.toPx()),
            )
        }
    }
}

/** 登录页/空状态用的大光环。 */
@Composable
fun BaHalo(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val w = size.width
        val h = size.height
        val center = Offset(w * 0.5f, h * 0.5f)
        drawCircle(
            color = BaSky.copy(alpha = 0.18f),
            radius = w * 0.48f,
            center = center,
        )
        drawCircle(
            color = BaSky.copy(alpha = 0.7f),
            radius = w * 0.42f,
            center = center,
            style = Stroke(width = 2.dp.toPx()),
        )
        drawCircle(
            color = BaSkyDeep.copy(alpha = 0.35f),
            radius = w * 0.33f,
            center = center,
            style = Stroke(width = 1.dp.toPx()),
        )
        // 光环四角的刻度。
        val tick = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round)
        listOf(0f, 90f, 180f, 270f).forEach { angle ->
            val rad = Math.toRadians(angle.toDouble())
            val startR = w * 0.44f
            val endR = w * 0.5f
            drawLine(
                color = BaSkyDeep.copy(alpha = 0.55f),
                start = Offset(
                    center.x + (startR * kotlin.math.cos(rad)).toFloat(),
                    center.y + (startR * kotlin.math.sin(rad)).toFloat(),
                ),
                end = Offset(
                    center.x + (endR * kotlin.math.cos(rad)).toFloat(),
                    center.y + (endR * kotlin.math.sin(rad)).toFloat(),
                ),
                strokeWidth = tick.width,
                cap = StrokeCap.Round,
            )
        }
        drawCircle(
            color = BaOrange.copy(alpha = 0.9f),
            radius = 3.dp.toPx(),
            center = Offset(w * 0.82f, h * 0.22f),
        )
    }
}

/** 主按钮：蓝色渐变胶囊 + 白色粗体字。 */
@Composable
fun BaButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    content: @Composable RowScope.() -> Unit,
) {
    CompositionLocalProvider(
        LocalContentColor provides Color.White,
        LocalTextStyle provides MaterialTheme.typography.labelLarge,
    ) {
        Row(
            modifier = modifier
                .alpha(if (enabled) 1f else 0.42f)
                .height(44.dp)
                .clip(BaPillShape)
                .background(Brush.horizontalGradient(listOf(BaSkyDeep, BaSky)))
                .border(1.dp, Color.White.copy(alpha = 0.35f), BaPillShape)
                .clickable(enabled = enabled) { onClick() }
                .padding(horizontal = 18.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
            content = content,
        )
    }
}

/** 次按钮：白底细蓝边胶囊 + 海军蓝文字。 */
@Composable
fun BaOutlineButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    content: @Composable RowScope.() -> Unit,
) {
    CompositionLocalProvider(
        LocalContentColor provides BaNavy,
        LocalTextStyle provides MaterialTheme.typography.labelLarge,
    ) {
        Row(
            modifier = modifier
                .alpha(if (enabled) 1f else 0.42f)
                .height(44.dp)
                .clip(BaPillShape)
                .background(Color.White)
                .border(1.dp, BaLine, BaPillShape)
                .clickable(enabled = enabled) { onClick() }
                .padding(horizontal = 18.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
            content = content,
        )
    }
}

/** 文字按钮：用于对话框。 */
@Composable
fun BaTextButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    content: @Composable RowScope.() -> Unit,
) {
    CompositionLocalProvider(
        LocalContentColor provides BaSkyDeep,
        LocalTextStyle provides MaterialTheme.typography.labelLarge,
    ) {
        Row(
            modifier = modifier
                .alpha(if (enabled) 1f else 0.42f)
                .height(40.dp)
                .clip(BaPillShape)
                .clickable(enabled = enabled) { onClick() }
                .padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
            content = content,
        )
    }
}

/** 筛选胶囊：选中为蓝底白字，未选中为白底细边。 */
@Composable
fun BaChip(
    selected: Boolean,
    onClick: () -> Unit,
    label: @Composable () -> Unit,
) {
    CompositionLocalProvider(
        LocalContentColor provides if (selected) Color.White else BaNavy,
        LocalTextStyle provides MaterialTheme.typography.labelMedium,
    ) {
        Row(
            modifier = Modifier
                .height(32.dp)
                .clip(BaPillShape)
                .background(if (selected) BaSkyDeep else Color.White)
                .border(1.dp, if (selected) BaSkyDeep else BaLine, BaPillShape)
                .clickable { onClick() }
                .padding(horizontal = 13.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (selected) {
                Box(
                    modifier = Modifier
                        .size(4.dp)
                        .background(Color.White, CircleShape),
                )
            }
            label()
        }
    }
}

/** 快捷标签（不参与选中态）。 */
@Composable
fun BaAssistChip(onClick: () -> Unit, label: @Composable () -> Unit) {
    CompositionLocalProvider(
        LocalContentColor provides BaNavy,
        LocalTextStyle provides MaterialTheme.typography.labelMedium,
    ) {
        Row(
            modifier = Modifier
                .height(32.dp)
                .clip(BaPillShape)
                .background(Color(0xFFF4FAFF))
                .border(1.dp, BaLine, BaPillShape)
                .clickable { onClick() }
                .padding(horizontal = 13.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            label()
        }
    }
}

/** 统一输入框：白底、细蓝边、蓝色光标。 */
@Composable
fun BaTextField(
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    label: (@Composable () -> Unit)? = null,
    placeholder: (@Composable () -> Unit)? = null,
    singleLine: Boolean = false,
    maxLines: Int = if (singleLine) 1 else Int.MAX_VALUE,
    minLines: Int = 1,
    visualTransformation: VisualTransformation = VisualTransformation.None,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    trailingIcon: (@Composable () -> Unit)? = null,
    isError: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier,
        enabled = enabled,
        label = label,
        placeholder = placeholder,
        singleLine = singleLine,
        maxLines = maxLines,
        minLines = minLines,
        visualTransformation = visualTransformation,
        keyboardOptions = keyboardOptions,
        trailingIcon = trailingIcon,
        isError = isError,
        shape = RoundedCornerShape(10.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = Color.White,
            unfocusedContainerColor = Color.White,
            disabledContainerColor = Color.White.copy(alpha = 0.6f),
            focusedBorderColor = BaSkyDeep,
            unfocusedBorderColor = BaLine,
            focusedLabelColor = BaSkyDeep,
            unfocusedLabelColor = BaNavySoft,
            focusedTextColor = BaNavy,
            unfocusedTextColor = BaNavy,
            cursorColor = BaSkyDeep,
            focusedPlaceholderColor = BaNavySoft.copy(alpha = 0.55f),
            unfocusedPlaceholderColor = BaNavySoft.copy(alpha = 0.55f),
        ),
    )
}

/** 统一滑杆配色。 */
@Composable
fun BaSlider(
    value: Float,
    onValueChange: (Float) -> Unit,
    modifier: Modifier = Modifier,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    steps: Int = 0,
) {
    Slider(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier,
        valueRange = valueRange,
        steps = steps,
        colors = SliderDefaults.colors(
            thumbColor = BaSkyDeep,
            activeTrackColor = BaSky,
            inactiveTrackColor = BaLine,
            activeTickColor = Color.White.copy(alpha = 0.6f),
            inactiveTickColor = Color.White.copy(alpha = 0.6f),
        ),
    )
}

/** 装饰用的小斜纹条。 */
@Composable
fun BaStripes(modifier: Modifier = Modifier, color: Color = BaSky.copy(alpha = 0.45f)) {
    Canvas(modifier = modifier) {
        val step = 5.dp.toPx()
        var x = -size.height
        while (x < size.width) {
            drawLine(
                color = color,
                start = Offset(x, size.height),
                end = Offset(x + size.height, 0f),
                strokeWidth = 1.6.dp.toPx(),
                cap = StrokeCap.Round,
            )
            x += step
        }
    }
}

enum class BaTabKind { Chat, Robot, Settings }

/** 底部导航图标，用 Canvas 手绘，避免引入额外图标包。 */
@Composable
fun BaTabIcon(kind: BaTabKind, selected: Boolean, modifier: Modifier = Modifier) {
    val tint = if (selected) BaSkyDeep else BaNavySoft.copy(alpha = 0.62f)
    Canvas(modifier = modifier.size(23.dp)) {
        val w = size.width
        val h = size.height
        val stroke = Stroke(
            width = 1.8.dp.toPx(),
            cap = StrokeCap.Round,
            join = StrokeJoin.Round,
        )
        when (kind) {
            BaTabKind.Chat -> {
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(w * 0.06f, h * 0.1f),
                    size = Size(w * 0.88f, h * 0.64f),
                    cornerRadius = CornerRadius(5.dp.toPx()),
                    style = stroke,
                )
                val tail = Path().apply {
                    moveTo(w * 0.28f, h * 0.74f)
                    lineTo(w * 0.26f, h * 0.94f)
                    lineTo(w * 0.5f, h * 0.74f)
                }
                drawPath(tail, tint, style = stroke)
                drawCircle(tint, 1.3.dp.toPx(), Offset(w * 0.32f, h * 0.42f))
                drawCircle(tint, 1.3.dp.toPx(), Offset(w * 0.5f, h * 0.42f))
                drawCircle(tint, 1.3.dp.toPx(), Offset(w * 0.68f, h * 0.42f))
            }
            BaTabKind.Robot -> {
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(w * 0.14f, h * 0.26f),
                    size = Size(w * 0.72f, h * 0.56f),
                    cornerRadius = CornerRadius(5.dp.toPx()),
                    style = stroke,
                )
                drawLine(
                    tint,
                    Offset(w * 0.5f, h * 0.26f),
                    Offset(w * 0.5f, h * 0.1f),
                    1.8.dp.toPx(),
                    StrokeCap.Round,
                )
                drawCircle(tint, 1.8.dp.toPx(), Offset(w * 0.5f, h * 0.09f))
                drawCircle(tint, 1.7.dp.toPx(), Offset(w * 0.35f, h * 0.5f))
                drawCircle(tint, 1.7.dp.toPx(), Offset(w * 0.65f, h * 0.5f))
                drawLine(
                    tint,
                    Offset(w * 0.38f, h * 0.68f),
                    Offset(w * 0.62f, h * 0.68f),
                    1.5.dp.toPx(),
                    StrokeCap.Round,
                )
            }
            BaTabKind.Settings -> {
                listOf(0.28f, 0.5f, 0.72f).forEachIndexed { index, y ->
                    drawLine(
                        color = tint,
                        start = Offset(w * 0.1f, h * y),
                        end = Offset(w * 0.9f, h * y),
                        strokeWidth = 1.8.dp.toPx(),
                        cap = StrokeCap.Round,
                    )
                    val knobX = when (index) {
                        0 -> w * 0.36f
                        1 -> w * 0.66f
                        else -> w * 0.44f
                    }
                    drawCircle(Color.White, 3.6.dp.toPx(), Offset(knobX, h * y))
                    drawCircle(
                        tint,
                        3.6.dp.toPx(),
                        Offset(knobX, h * y),
                        style = Stroke(width = 1.8.dp.toPx()),
                    )
                }
            }
        }
    }
}
