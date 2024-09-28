use crate::Success;
use std::collections::HashMap;

#[derive(Debug, serde::Serialize)]
pub struct ChangelogBehaviour {
    update: bool,
    explanation: String,
}

#[derive(Debug, serde::Serialize)]
pub struct Context {
    changelog: Option<ChangelogBehaviour>,
}

pub fn report_success(
    versions: HashMap<String, String>,
    value: Option<i32>,
    context: Option<serde_json::Value>,
    changelog: Option<(bool, String)>,
) {
    if std::env::var("SVP_API").ok().as_deref() == Some("1") {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        serde_json::to_writer(
            f,
            &Success {
                versions,
                value,
                context,
                debian: Some(Context {
                    changelog: changelog.map(|cl| ChangelogBehaviour {
                        update: cl.0,
                        explanation: cl.1,
                    }),
                }),
            },
        )
        .unwrap();
    }
}
