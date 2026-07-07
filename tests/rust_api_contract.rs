use axum::body::Body;
use axum::http::{Request, StatusCode};
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::settings::ManagerSettings;
use http_body_util::BodyExt;
use serde_json::Value;
use tokio_tungstenite::tungstenite::Error as WsError;
use tower::ServiceExt;

#[tokio::test]
async fn settings_api_get_and_patch() {
    let state = AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        Some("secret".to_string()),
    );
    let app = app(state);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let payload = json_body(response).await;
    assert_eq!(payload["refresh_interval"], 1.0);
    assert_eq!(
        payload["allowed_refresh_intervals"],
        serde_json::json!([0.5, 1.0, 2.0, 5.0])
    );
    assert_eq!(payload["process_interval"], 3.0);

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/api/settings")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"refresh_interval":0.5}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let payload = json_body(response).await;
    assert_eq!(payload["refresh_interval"], 0.5);
}

#[tokio::test]
async fn settings_api_rejects_unsupported_refresh_interval() {
    let app = app(AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    ));

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/api/settings")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"refresh_interval":3.0}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn deprecated_single_node_http_api_returns_gone() {
    let app = app(AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    ));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/snapshot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::GONE);
    let payload = json_body(response).await;
    assert!(payload["detail"]
        .as_str()
        .unwrap()
        .contains("/api/cluster/snapshot"));
}

#[tokio::test]
async fn disabled_optional_apis_match_python_without_db() {
    let app = app(AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    ));

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/history/gpu")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        json_body(response).await,
        serde_json::json!({"enabled": false, "items": []})
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/analytics/overview")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        json_body(response).await,
        serde_json::json!({"enabled": false})
    );
}

#[tokio::test]
async fn frontend_dist_is_served_with_spa_fallback() {
    let dir = tempfile::tempdir().unwrap();
    let dist = dir.path().join("dist");
    std::fs::create_dir_all(dist.join("assets")).unwrap();
    std::fs::write(
        dist.join("index.html"),
        "<!doctype html><title>Constella</title>",
    )
    .unwrap();
    std::fs::write(dist.join("assets").join("app.js"), "console.log('ok');").unwrap();

    let app = app(AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_frontend_dist(Some(dist)));

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/overview")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = text_body(response).await;
    assert!(body.contains("Constella"));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/assets/app.js")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = text_body(response).await;
    assert_eq!(body, "console.log('ok');");
}

#[tokio::test]
async fn highres_stream_rejects_missing_token_when_configured() {
    let app = app(AppState::new(
        ClusterState::new("manager".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_highres_token(Some("secret".to_string())));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });

    let error = tokio_tungstenite::connect_async(format!("ws://{addr}/api/highres/stream"))
        .await
        .unwrap_err();
    server.abort();

    match error {
        WsError::Http(response) => assert_eq!(response.status(), StatusCode::UNAUTHORIZED),
        other => panic!("expected HTTP 401, got {other:?}"),
    }
}

async fn json_body(response: axum::response::Response) -> Value {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn text_body(response: axum::response::Response) -> String {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    String::from_utf8(bytes.to_vec()).unwrap()
}
