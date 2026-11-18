use constella_protocol::{ControlRequest, ControlResponse};
use std::collections::HashMap;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::sync::{mpsc, oneshot};

#[derive(Default)]
pub struct Broker {
    connections: Mutex<HashMap<String, Connection>>,
    ids: AtomicU64,
}

struct Connection {
    id: u64,
    instance: String,
    tx: mpsc::Sender<Call>,
}

pub struct Call {
    pub id: u64,
    pub request: ControlRequest,
    pub reply: oneshot::Sender<ControlResponse>,
}

#[derive(Debug)]
pub enum Error {
    Unavailable,
    Timeout,
}

pub struct Registration<'a> {
    broker: &'a Broker,
    node: String,
    connection: u64,
}

impl Drop for Registration<'_> {
    fn drop(&mut self) {
        let mut connections = self.broker.connections.lock().unwrap();
        if connections
            .get(&self.node)
            .is_some_and(|entry| entry.id == self.connection)
        {
            connections.remove(&self.node);
        }
    }
}

impl Broker {
    pub fn connect(
        &self,
        node: String,
        id: u64,
        instance: String,
    ) -> (Registration<'_>, mpsc::Receiver<Call>) {
        let (tx, rx) = mpsc::channel(8);
        let registration = Registration {
            broker: self,
            node: node.clone(),
            connection: id,
        };
        self.connections
            .lock()
            .unwrap()
            .insert(node, Connection { id, instance, tx });
        (registration, rx)
    }

    pub fn instance(&self, node: &str) -> Option<String> {
        self.connections
            .lock()
            .unwrap()
            .get(node)
            .map(|connection| connection.instance.clone())
    }

    pub async fn request(
        &self,
        node: &str,
        request: ControlRequest,
        timeout: Duration,
        expected_instance: Option<&str>,
    ) -> Result<ControlResponse, Error> {
        let (reply, rx) = oneshot::channel();
        let id = self.ids.fetch_add(1, Ordering::Relaxed);
        {
            let connections = self.connections.lock().unwrap();
            let connection = connections
                .get(node)
                .filter(|connection| expected_instance.is_none_or(|id| id == connection.instance))
                .ok_or(Error::Unavailable)?;
            connection
                .tx
                .try_send(Call { id, request, reply })
                .map_err(|_| Error::Unavailable)?;
        }
        tokio::time::timeout(timeout, rx)
            .await
            .map_err(|_| Error::Timeout)?
            .map_err(|_| Error::Unavailable)
    }
}
