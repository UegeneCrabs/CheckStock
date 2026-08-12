from app.dto.stores import Store

STORES: dict[str, Store] = {
    "rimili": Store(name="RIMILI", color="#E63946", initials="RI", text="#fff"),
    "tris": Store(name="TRIS", color="#2A9D8F", initials="TR", text="#fff"),
    "rockkiddo": Store(name="ROCKKIDDO", color="#E9C46A", initials="RK", text="#1f2a44"),
    "trusthome": Store(name="TRUSTHOME", color="#8338EC", initials="TH", text="#fff"),
    "sokoloff": Store(name="SOKOLOFF", color="#3A86FF", initials="SO", text="#fff"),
    "gogol": Store(name="GOGOL", color="#06D6A0", initials="GO", text="#fff"),
    "toyka": Store(name="TOYKA", color="#FF6D00", initials="TO", text="#fff"),
}
