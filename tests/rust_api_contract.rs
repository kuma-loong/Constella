use axum::body::Body;
use axum::http::{Request, StatusCode};
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::settings::ManagerSettings;
use http_body_util::BodyExt;
use serde_json::Value;
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

async fn json_body(response: axum::response::Response) -> Value {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}
