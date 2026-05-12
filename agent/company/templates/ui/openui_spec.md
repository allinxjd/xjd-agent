# OpenUI-Lang Specification

OpenUI-Lang is a compact, streaming-first declarative UI language. It uses XML-like syntax with semantic component names.

## Core Principles
- 67% more token-efficient than equivalent JSON/HTML
- Designed for LLM streaming output
- Self-contained — no external CSS/JS dependencies needed
- Semantic component names map directly to UI primitives

## Component Reference

### Layout
```
<Stack direction="vertical|horizontal" gap="sm|md|lg" padding="sm|md|lg">
<Row gap="sm|md|lg" align="center|start|end" justify="start|center|end|between">
<Column width="1/2|1/3|2/3|1/4|3/4|full">
<Card padding="sm|md|lg" shadow="sm|md|lg" radius="sm|md|lg">
<Section title="Section Title">
<Divider/>
<Spacer size="sm|md|lg"/>
```

### Typography
```
<Heading level="1|2|3|4">Page Title</Heading>
<Text size="sm|md|lg" weight="normal|medium|bold" color="muted|primary|danger|success">
<Label for="field-id">Field Label</Label>
<Code>inline code</Code>
```

### Form Controls
```
<Input type="text|email|password|number|search|tel" placeholder="..." label="..." required/>
<Textarea placeholder="..." rows="3" label="..."/>
<Select label="..."><Option value="v1">Label 1</Option></Select>
<Checkbox label="Accept terms" checked/>
<Radio name="group" value="opt1" label="Option 1"/>
<Switch label="Enable notifications" checked/>
<Button variant="primary|secondary|outline|danger|ghost" size="sm|md|lg" icon="...">
```

### Data Display
```
<Badge variant="info|success|warning|danger" size="sm|md">
<Avatar src="url" size="sm|md|lg" name="Fallback Name"/>
<Table><Thead><Tr><Th>Col</Th></Tr></Thead><Tbody><Tr><Td>Val</Td></Tr></Tbody></Table>
<List ordered><ListItem>Item text</ListItem></List>
<Stat label="Total Users" value="1,234" change="+12%"/>
<Tag>Category</Tag>
```

### Navigation
```
<Tabs active="tab1"><Tab id="tab1">Tab 1</Tab><Tab id="tab2">Tab 2</Tab></Tabs>
<Breadcrumb><BreadcrumbItem href="/">Home</BreadcrumbItem></Breadcrumb>
<Nav><NavItem href="/" active>Home</NavItem></Nav>
```

### Feedback
```
<Alert variant="info|success|warning|danger" title="Optional Title">Message</Alert>
<Progress value="60" max="100" label="Upload progress"/>
<Skeleton width="100%" height="20px"/>
<Toast variant="success">Saved successfully</Toast>
<Modal title="Confirm" open><Text>Are you sure?</Text></Modal>
```

### Media
```
<Image src="url" alt="description" width="200" height="150" radius="md"/>
<Icon name="search|home|settings|user|bell|menu" size="sm|md|lg"/>
```

## Props Convention
- All props use `key="value"` syntax (double quotes)
- Boolean props: `checked`, `required`, `disabled`, `open`
- Self-closing tags for void elements: `<Input/>`, `<Divider/>`, `<Spacer/>`
- Nesting follows logical hierarchy: Page > Section > Card > Stack > Elements

## Example: Login Form

```openui
<Card padding="lg" shadow="md" radius="lg">
  <Stack gap="lg">
    <Heading level="2">Welcome Back</Heading>
    <Text color="muted">Sign in to your account</Text>
    <Stack gap="md">
      <Input type="email" label="Email" placeholder="you@example.com" required/>
      <Input type="password" label="Password" placeholder="Enter password" required/>
      <Row justify="between" align="center">
        <Checkbox label="Remember me"/>
        <Button variant="ghost" size="sm">Forgot password?</Button>
      </Row>
    </Stack>
    <Button variant="primary" size="lg">Sign In</Button>
    <Text size="sm" color="muted">Don't have an account? <Button variant="ghost" size="sm">Sign up</Button></Text>
  </Stack>
</Card>
```

## Output Rules
- Output ONLY openui-lang code
- No markdown fences, no explanation text
- Use Chinese text for labels/placeholders when the project requirement is in Chinese
- Keep structure clean and well-indented (2 spaces)
- One component per line for readability
